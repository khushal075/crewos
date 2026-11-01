"""Tenant-scoped durable status, cancellation, idempotency, and resume APIs."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from crewos.api.agent_routes import router
from crewos.core.config import settings
from crewos.infrastructure.run_store import SQLiteRunStore, get_run_store
from crewos.infrastructure.task_ownership import TaskOwnershipStore, get_task_store
from tests.helpers import MemoryRedis


def headers(tenant="tenant_a"):
    return {"X-Tenant-ID": tenant, "Authorization": "Bearer " + tenant}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(
        settings, "TENANT_API_KEYS", {t: SecretStr(t) for t in ("tenant_a", "tenant_b")}
    )
    grants = frozenset(
        {"agent_run.create", "agent_run.read", "agent_run.cancel", "agent_run.resume"}
    )
    monkeypatch.setattr(settings, "TENANT_PERMISSIONS", {"tenant_a": grants, "tenant_b": grants})
    store = SQLiteRunStore(str(tmp_path / "runs.sqlite3"))
    legacy = TaskOwnershipStore(MemoryRedis())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_run_store] = lambda: store
    app.dependency_overrides[get_task_store] = lambda: legacy
    with patch("crewos.api.agent_routes.run_agent.apply_async") as publish:
        yield TestClient(app), store, legacy, publish


def submit(client, **kwargs):
    response = client.post(
        "/agents/runs", headers=headers(), json={"message": "Inspect a task"}, **kwargs
    )
    assert response.status_code == 202
    return response.json()["agent_run_id"]


def test_status_survives_loss_of_legacy_ownership_and_hides_checkpoint(setup):
    client, store, legacy, _ = setup
    identifier = submit(client)
    record = store.get(identifier, "tenant_a")
    claim = store.claim(record["job"])
    store.checkpoint(
        identifier, "tenant_a", claim["lease_token"], {"history": [{"content": "private-input"}]}
    )
    store.finish(
        identifier,
        "tenant_a",
        claim["lease_token"],
        state="succeeded",
        result={"type": "final_answer", "answer": "Done"},
    )
    legacy.client.values.clear()
    response = client.get("/agents/runs/" + identifier, headers=headers())
    assert response.status_code == 200
    assert response.json()["state"] == "succeeded"
    assert response.json()["result"]["answer"] == "Done"
    assert "private-input" not in response.text
    assert "lease_token" not in response.text
    assert len(response.json()["attempt_history"]) == 1
    assert client.get("/agents/runs/" + identifier, headers=headers("tenant_b")).status_code == 404


def test_queued_cancel_is_idempotent_and_blocks_execution(setup):
    client, _, _, publish = setup
    identifier = submit(client)
    assert (
        client.post(f"/agents/runs/{identifier}/cancel", headers=headers()).json()["state"]
        == "cancelled"
    )
    assert client.post(f"/agents/runs/{identifier}/cancel", headers=headers()).status_code == 202
    assert client.post(f"/agents/runs/{identifier}/resume", headers=headers()).status_code == 409
    assert publish.call_count == 1


def test_explicit_resume_republishes_original_payload_and_id(setup):
    client, store, _, publish = setup
    identifier = submit(client)
    original = store.get(identifier, "tenant_a")
    claim = store.claim(original["job"])
    store.finish(identifier, "tenant_a", claim["lease_token"], state="failed", error="RuntimeError")
    response = client.post(f"/agents/runs/{identifier}/resume", headers=headers())
    assert response.status_code == 202
    assert publish.call_args.kwargs["task_id"] == identifier
    assert publish.call_args.kwargs["args"] == [original["job"]]
    assert store.get(identifier, "tenant_a")["deadline"] == claim["deadline"]


def test_repeated_submission_key_does_not_publish_again(setup):
    client, _, _, publish = setup
    request_headers = {**headers(), "Idempotency-Key": "same-request"}
    first = client.post("/agents/runs", headers=request_headers, json={"message": "Inspect"})
    second = client.post("/agents/runs", headers=request_headers, json={"message": "Inspect"})
    assert first.status_code == second.status_code == 202
    assert second.json()["replayed"] is True
    assert first.json()["agent_run_id"] == second.json()["agent_run_id"]
    assert publish.call_count == 1
    changed = client.post("/agents/runs", headers=request_headers, json={"message": "Different"})
    assert changed.status_code == 409


@pytest.mark.parametrize(
    "operation,method", [("", "get"), ("/cancel", "post"), ("/resume", "post")]
)
def test_durable_operations_require_their_own_permission(setup, monkeypatch, operation, method):
    client, _, _, _ = setup
    identifier = submit(client)
    monkeypatch.setattr(
        settings, "TENANT_PERMISSIONS", {"tenant_a": frozenset({"agent_run.create"})}
    )
    assert (
        getattr(client, method)(
            f"/agents/runs/{identifier}{operation}", headers=headers()
        ).status_code
        == 403
    )


def test_submission_budget_is_bounded_by_server_policy(setup, monkeypatch):
    client, _, _, publish = setup
    monkeypatch.setattr(settings, "AGENT_MAX_DECISIONS", 3)
    response = client.post(
        "/agents/runs", headers=headers(), json={"message": "Inspect", "max_decisions": 4}
    )
    assert response.status_code == 422
    publish.assert_not_called()


def test_idempotent_retry_after_uncertain_publish_reuses_run_id(setup):
    client, store, _, publish = setup
    request_headers = {**headers(), "Idempotency-Key": "uncertain-send"}
    publish.side_effect = RuntimeError("broker disconnected after possible delivery")
    first = client.post("/agents/runs", headers=request_headers, json={"message": "Inspect"})
    assert first.status_code == 500
    original_id = publish.call_args.kwargs["task_id"]
    assert not store.get(original_id, "tenant_a")["published"]
    publish.side_effect = None
    second = client.post("/agents/runs", headers=request_headers, json={"message": "Inspect"})
    assert second.status_code == 202
    assert second.json()["agent_run_id"] == original_id
    assert second.json()["replayed"] is True
    assert publish.call_args.kwargs["task_id"] == original_id
    assert store.get(original_id, "tenant_a")["published"]


def test_oversized_message_is_rejected_before_persistence_and_publication(setup):
    client, _, _, publish = setup
    response = client.post("/agents/runs", headers=headers(), json={"message": "x" * 32769})
    assert response.status_code == 422
    publish.assert_not_called()
