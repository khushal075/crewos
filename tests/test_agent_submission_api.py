"""Real authentication and policy protect queue publication and ownership."""

from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError

from crewos.api.agent_routes import router
from crewos.core.config import settings
from crewos.core.tenant import tenant_queue
from crewos.infrastructure.task_ownership import TaskOwnershipStore, get_task_store
from tests.helpers import MemoryRedis
from crewos.infrastructure.run_store import SQLiteRunStore, get_run_store


def headers(token="test-a"):
    return {"X-Tenant-ID": "tenant_a", "Authorization": "Bearer " + token}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "TENANT_API_KEYS", {"tenant_a": SecretStr("test-a")})
    monkeypatch.setattr(
        settings, "TENANT_PERMISSIONS", {"tenant_a": frozenset({"agent_run.create"})}
    )
    store = TaskOwnershipStore(MemoryRedis())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_task_store] = lambda: store
    durable = SQLiteRunStore(str(tmp_path / "runs.sqlite3"))
    app.dependency_overrides[get_run_store] = lambda: durable
    with patch("crewos.api.agent_routes.run_agent.apply_async") as publish:
        yield TestClient(app), store, publish


def test_submission_registers_ownership_before_publication_and_assigns_identity(setup):
    client, store, publish = setup

    def assert_owned(**kwargs):
        store.require_owner(kwargs["task_id"], "tenant_a")

    publish.side_effect = assert_owned
    response = client.post("/agents/runs", headers=headers(), json={"message": "Inspect task-123"})
    assert response.status_code == 202
    result = response.json()
    assert result["status"] == "queued"
    assert UUID(result["agent_run_id"]).version == 4
    publish.assert_called_once_with(
        args=[
            {
                "agent_run_id": result["agent_run_id"],
                "tenant_id": "tenant_a",
                "message": "Inspect task-123",
                "max_decisions": 5,
            }
        ],
        task_id=result["agent_run_id"],
        queue=tenant_queue("tenant_a"),
        expires=settings.TASK_QUEUE_TTL,
        retry=False,
    )


@pytest.mark.parametrize(
    "request_headers,status",
    [
        ({"X-Tenant-ID": "tenant_a"}, 401),
        (headers("wrong"), 403),
    ],
)
def test_failed_authentication_never_registers_or_publishes(setup, request_headers, status):
    client, store, publish = setup
    response = client.post("/agents/runs", headers=request_headers, json={"message": "hello"})
    assert response.status_code == status
    assert not store.client.values
    publish.assert_not_called()


def test_tool_read_permission_does_not_grant_run_creation(setup, monkeypatch):
    client, store, publish = setup
    monkeypatch.setattr(
        settings, "TENANT_PERMISSIONS", {"tenant_a": frozenset({"agent_task.read"})}
    )
    assert (
        client.post("/agents/runs", headers=headers(), json={"message": "hello"}).status_code == 403
    )
    assert not store.client.values
    publish.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": " "},
        {"message": "hello", "tenant_id": "tenant_b"},
        {"message": "hello", "permissions": ["agent_run.create"]},
        {"message": "hello", "agent_run_id": "chosen"},
        {"message": "hello", "max_decisions": True},
    ],
)
def test_invalid_or_forged_request_fields_never_publish(setup, payload):
    client, store, publish = setup
    assert client.post("/agents/runs", headers=headers(), json=payload).status_code == 422
    assert not store.client.values
    publish.assert_not_called()


def test_storage_failure_prevents_publication(setup):
    client, store, publish = setup
    with patch.object(store.client, "set", side_effect=RedisConnectionError("private detail")):
        response = client.post("/agents/runs", headers=headers(), json={"message": "hello"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Task storage unavailable"}
    publish.assert_not_called()


def test_ambiguous_publish_failure_preserves_registration(setup):
    client, store, publish = setup
    publish.side_effect = RuntimeError("private broker detail")
    response = client.post("/agents/runs", headers=headers(), json={"message": "hello"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to submit agent run"}
    run_id = publish.call_args.kwargs["task_id"]
    store.require_owner(run_id, "tenant_a")


def test_policy_failure_never_registers_or_publishes(setup):
    client, store, publish = setup
    with patch(
        "crewos.api.agent_routes.PermissionPolicyService.evaluate",
        side_effect=RuntimeError("private"),
    ):
        response = client.post("/agents/runs", headers=headers(), json={"message": "hello"})
    assert response.status_code == 503
    assert not store.client.values
    publish.assert_not_called()


def test_main_application_exposes_agent_submission():
    from crewos.main import app

    assert "/agents/runs" in app.openapi()["paths"]


def test_document_mode_is_backend_recorded_and_published(setup):
    client, _, publish = setup
    response = client.post(
        "/agents/runs",
        headers=headers(),
        json={"message": "Find guidance", "answer_mode": "document"},
    )
    assert response.status_code == 202
    assert publish.call_args.kwargs["args"][0]["answer_mode"] == "document"


def test_invalid_answer_mode_is_rejected_before_publication(setup):
    client, _, publish = setup
    response = client.post(
        "/agents/runs",
        headers=headers(),
        json={"message": "Find guidance", "answer_mode": "unrestricted"},
    )
    assert response.status_code == 422
    publish.assert_not_called()
