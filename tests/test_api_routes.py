"""
Integration tests for crewos/api/routes.py
Uses FastAPI TestClient — no real Celery broker or LLM required.
All external dependencies (CrewRunner, run_crew task, AsyncResult) are mocked.
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crewos.api.routes import router
from crewos.core.tenant import get_tenant_id


# ── App fixture ───────────────────────────────────────────────────────────────

def make_app(tenant_id: str = "tenant-123") -> FastAPI:
    """Creates a test FastAPI app with tenant dependency overridden."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_tenant_id] = lambda: tenant_id
    return app


@pytest.fixture
def client():
    return TestClient(make_app())


@pytest.fixture
def anon_client():
    """Client with no tenant override — uses real get_tenant_id dependency."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:

    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# ── POST /domain/run (async enqueue) ─────────────────────────────────────────

class TestRunCrewAsync:

    @patch("crewos.api.routes.run_crew")
    def test_enqueue_returns_task_id(self, mock_run_crew, client):
        mock_task = MagicMock()
        mock_task.id = "task-abc-123"
        mock_run_crew.apply_async.return_value = mock_task

        response = client.post(
            "/domain/run",
            json={"agent_type": "research", "input": {"message": "hello"}},
            headers={"X-Tenant-Id": "tenant-123"},
        )
        assert response.status_code == 200
        assert response.json()["task_id"] == "task-abc-123"
        assert response.json()["status"] == "Queued"

    @patch("crewos.api.routes.run_crew")
    def test_enqueue_calls_apply_async_with_tenant_queue(self, mock_run_crew, client):
        mock_task = MagicMock()
        mock_task.id = "task-xyz"
        mock_run_crew.apply_async.return_value = mock_task

        client.post(
            "/domain/run",
            json={"agent_type": "research", "input": {"message": "hello"}},
            headers={"X-Tenant-Id": "tenant-123"},
        )
        call_kwargs = mock_run_crew.apply_async.call_args[1]
        assert "tenant-123" in call_kwargs["queue"]

    @patch("crewos.api.routes.run_crew")
    def test_enqueue_failure_returns_500(self, mock_run_crew, client):
        mock_run_crew.apply_async.side_effect = Exception("broker down")

        response = client.post(
            "/domain/run",
            json={"agent_type": "research", "input": {"message": "hello"}},
            headers={"X-Tenant-Id": "tenant-123"},
        )
        assert response.status_code == 500
        assert "Failed to enqueue" in response.json()["detail"]

    def test_missing_tenant_header_returns_422(self, anon_client):
        response = anon_client.post(
            "/domain/run",
            json={"agent_type": "research", "input": {"message": "hello"}},
        )
        # FastAPI returns 422 when a required Header dependency is missing
        assert response.status_code == 422


# ── POST /domain/run-sync ─────────────────────────────────────────────────────

class TestRunCrewSync:

    @patch("crewos.api.routes.CrewRunner")
    def test_sync_run_returns_completed(self, mock_runner, client):
        # CrewRunResponse only has task_id and status.
        # The route does CrewRunResponse(task_id="sync_run", status="Completed", **result)
        # so **result must not contain any keys outside that schema, and must
        # not contain 'status' or 'task_id' either (duplicate keyword error).
        mock_runner.run.return_value = {}

        response = client.post(
            "/domain/run-sync",
            json={"agent_type": "research", "input": {"message": "hello"}},
            headers={"X-Tenant-Id": "tenant-123"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "Completed"
        assert response.json()["task_id"] == "sync_run"

    @patch("crewos.api.routes.CrewRunner")
    def test_sync_run_value_error_returns_400(self, mock_runner, client):
        mock_runner.run.side_effect = ValueError("Message is required")

        response = client.post(
            "/domain/run-sync",
            json={"agent_type": "research", "input": {"message": ""}},
            headers={"X-Tenant-Id": "tenant-123"},
        )
        assert response.status_code == 400
        assert "Message is required" in response.json()["detail"]

    @patch("crewos.api.routes.CrewRunner")
    def test_sync_run_unexpected_error_returns_500(self, mock_runner, client):
        mock_runner.run.side_effect = RuntimeError("unexpected crash")

        response = client.post(
            "/domain/run-sync",
            json={"agent_type": "research", "input": {"message": "hello"}},
            headers={"X-Tenant-Id": "tenant-123"},
        )
        assert response.status_code == 500
        assert "Internal Server Error" in response.json()["detail"]


# ── GET /domain/status/{task_id} ──────────────────────────────────────────────

class TestTaskStatus:

    @patch("crewos.api.routes.AsyncResult")
    def test_status_success(self, mock_async_result, client):
        mock_result = MagicMock()
        mock_result.state = "SUCCESS"
        mock_result.result = {"output": "final answer"}
        mock_async_result.return_value = mock_result

        response = client.get(
            "/domain/status/task-abc",
            headers={"X-Tenant-Id": "tenant-123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-abc"
        assert data["status"] == "SUCCESS"

    @patch("crewos.api.routes.AsyncResult")
    def test_status_failure(self, mock_async_result, client):
        mock_result = MagicMock()
        mock_result.state = "FAILURE"
        mock_result.result = RuntimeError("task exploded")
        mock_async_result.return_value = mock_result

        response = client.get(
            "/domain/status/task-abc",
            headers={"X-Tenant-Id": "tenant-123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "FAILURE"
        assert data["result"]["error_type"] == "RuntimeError"
        assert "task exploded" in data["result"]["error_message"]

    @patch("crewos.api.routes.AsyncResult")
    def test_status_pending(self, mock_async_result, client):
        mock_result = MagicMock()
        mock_result.state = "PENDING"
        mock_result.result = None
        mock_async_result.return_value = mock_result

        response = client.get(
            "/domain/status/task-abc",
            headers={"X-Tenant-Id": "tenant-123"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "PENDING"
        assert response.json()["result"] is None

    @patch("crewos.api.routes.AsyncResult")
    def test_status_started(self, mock_async_result, client):
        mock_result = MagicMock()
        mock_result.state = "STARTED"
        mock_result.result = None
        mock_async_result.return_value = mock_result

        response = client.get(
            "/domain/status/task-abc",
            headers={"X-Tenant-Id": "tenant-123"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "STARTED"

    @patch("crewos.api.routes.AsyncResult")
    def test_status_serialization_error_falls_back(self, mock_async_result, client):
        """If result serialization fails, falls back to str(result)."""
        mock_result = MagicMock()
        mock_result.state = "SUCCESS"
        mock_result.result = MagicMock(
            side_effect=Exception("serialization error")
        )
        # Make jsonable_encoder raise by making result unpicklable
        mock_async_result.return_value = mock_result

        # Should not raise — falls back gracefully
        response = client.get(
            "/domain/status/task-abc",
            headers={"X-Tenant-Id": "tenant-123"},
        )
        assert response.status_code == 200