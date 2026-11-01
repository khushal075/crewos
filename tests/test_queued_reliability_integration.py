"""Actual Celery delivery over memory transport with durable SQLite records.

Model and target-state responses are controlled; no live model/Redis is contacted.
"""

from types import SimpleNamespace
from unittest.mock import Mock
import time

from celery import Celery
from celery.contrib.testing.worker import start_worker
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from crewos.api.agent_routes import router
from crewos.core.config import settings
from crewos.core.tenant import tenant_queue
from crewos.infrastructure.run_store import SQLiteRunStore, get_run_store
from crewos.infrastructure.task_ownership import TaskOwnershipStore, get_task_store
from crewos.workers.agent_task import run_agent
from tests.helpers import MemoryRedis


def test_http_to_celery_to_durable_result_and_duplicate_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "TENANT_API_KEYS", {"tenant_a": SecretStr("integration-token")})
    monkeypatch.setattr(
        settings,
        "TENANT_PERMISSIONS",
        {"tenant_a": frozenset({"agent_run.create", "agent_run.read", "agent_task.read"})},
    )
    durable = SQLiteRunStore(str(tmp_path / "runs.sqlite3"))
    ownership = TaskOwnershipStore(MemoryRedis())
    ownership.register("task-123", "tenant_a")
    model = Mock()
    model.invoke.side_effect = [
        AIMessage(
            content='{"type":"tool_call","name":"get_agent_task_status","arguments":{"agent_task_id":"task-123"}}'
        ),
        AIMessage(content='{"type":"final_answer","answer":"Succeeded."}'),
    ]
    monkeypatch.setattr("crewos.core.agent_job_runner.get_run_store", lambda: durable)
    monkeypatch.setattr("crewos.core.agent_job_runner.get_task_store", lambda: ownership)
    monkeypatch.setattr("crewos.core.agent_job_runner.OllamaLLMProvider.get", lambda self: model)
    backend = Mock(return_value=SimpleNamespace(state="SUCCESS"))
    monkeypatch.setattr("crewos.infrastructure.task_status_reader.AsyncResult", backend)
    transport = Celery(
        "point4-integration", broker="memory://", backend="cache+memory://", set_as_current=False
    )
    transport.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"])
    delivered = transport.task(
        bind=True, name="point4.run_agent", max_retries=0, resultrepr_maxsize=0
    )(run_agent.run.__func__)
    monkeypatch.setattr("crewos.api.agent_routes.run_agent", delivered)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_run_store] = lambda: durable
    app.dependency_overrides[get_task_store] = lambda: ownership
    client = TestClient(app)
    headers = {"X-Tenant-ID": "tenant_a", "Authorization": "Bearer integration-token"}
    with start_worker(
        transport,
        perform_ping_check=False,
        pool="solo",
        queues=[tenant_queue("tenant_a")],
        shutdown_timeout=10,
    ):
        accepted = client.post("/agents/runs", headers=headers, json={"message": "Check task-123"})
        assert accepted.status_code == 202
        identifier = accepted.json()["agent_run_id"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            response = client.get("/agents/runs/" + identifier, headers=headers)
            if response.json()["state"] in {"succeeded", "failed", "exhausted"}:
                break
            time.sleep(0.05)
        assert response.json()["state"] == "succeeded", response.json()
        assert response.json()["result"]["answer"] == "Succeeded."
        assert response.json()["attempts"] == 1
        record = durable.get(identifier, "tenant_a")
        duplicate = delivered.apply_async(
            args=[record["job"]], task_id=identifier, queue=tenant_queue("tenant_a")
        )
        duplicate.get(timeout=5, disable_sync_subtasks=False)
        assert model.invoke.call_count == 2
        assert durable.get(identifier, "tenant_a")["attempts"] == 1
        backend.assert_called_once()
