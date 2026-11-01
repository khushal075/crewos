"""Real authentication protects document lifecycle, retries, and job publication."""

from unittest.mock import patch
import sqlite3
import json
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest

from crewos.api import document_routes
from crewos.api.middleware.document_body_limit import DocumentBodyLimitMiddleware
from crewos.core import document_setup
from crewos.core.config import settings
from crewos.core.tenant import tenant_queue
from crewos.ingestion import build_sqlite_document_store
from crewos.workers.document_task import run_document_job

GRANTS = frozenset(
    {
        "document.create",
        "document.update",
        "document.read",
        "document.delete",
        "document.job.read",
        "document.job.retry",
    }
)


def headers(tenant="tenant_a", key="request-1", token=None):
    return {
        "X-Tenant-ID": tenant,
        "Authorization": "Bearer " + (token or ("test-a" if tenant == "tenant_a" else "test-b")),
        "Idempotency-Key": key,
    }


def body(text="Private recovery instructions."):
    return {"title": "Private runbook title", "text": text, "source_ref": "private-source-label"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    store = build_sqlite_document_store(tmp_path / "docs.sqlite3")
    monkeypatch.setattr(
        settings,
        "TENANT_API_KEYS",
        {"tenant_a": SecretStr("test-a"), "tenant_b": SecretStr("test-b")},
    )
    monkeypatch.setattr(settings, "TENANT_PERMISSIONS", {"tenant_a": GRANTS, "tenant_b": GRANTS})
    monkeypatch.setattr(document_setup, "get_document_store", lambda: store)
    app = FastAPI()
    app.add_middleware(DocumentBodyLimitMiddleware)
    app.include_router(document_routes.router)
    app.dependency_overrides[document_routes.get_document_store] = lambda: store
    with patch.object(document_routes.run_document_job, "apply_async") as publish:
        yield TestClient(app), store, publish


def deliver(job_id, tenant="tenant_a"):
    run_document_job.push_request(id=job_id, delivery_info={"routing_key": tenant_queue(tenant)})
    try:
        return run_document_job.run({"job_id": job_id, "tenant_id": tenant})
    finally:
        run_document_job.pop_request()


def test_sync_create_update_read_delete_and_cached_deleted_receipt(api):
    client, store, publish = api
    first = client.post("/documents", headers=headers(), json=body())
    assert first.status_code == 200, first.text
    receipt = first.json()
    document_id = receipt["job"]["document_id"]
    assert receipt["job"]["state"] == "succeeded"
    assert (
        client.get(f"/documents/{document_id}", headers=headers()).json()["document"]["text"]
        == body()["text"]
    )
    edit = client.put(
        f"/documents/{document_id}",
        headers=headers(key="edit-1"),
        json={**body("Replacement"), "expected_version": 1},
    )
    assert edit.status_code == 200 and edit.json()["job"]["version"] == 2
    assert (
        client.get(f"/documents/{document_id}?version=1", headers=headers()).json()["version"] == 1
    )
    assert client.get(f"/documents/{document_id}", headers=headers()).json()["version"] == 2
    assert client.delete(f"/documents/{document_id}", headers=headers()).status_code == 204
    assert client.get(f"/documents/{document_id}", headers=headers()).status_code == 404
    replay = client.post("/documents", headers=headers(), json=body())
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert replay.json()["job"]["job_id"] == receipt["job"]["job_id"]
    assert not replay.json()["job"]["result_available"]
    publish.assert_not_called()


def test_duplicate_submit_reuses_receipt_and_changed_payload_conflicts(api):
    client, store, publish = api
    first = client.post("/documents", headers=headers(), json=body()).json()
    replay = client.post("/documents", headers=headers(), json=body()).json()
    assert replay["replayed"] and replay["job"]["job_id"] == first["job"]["job_id"]
    assert client.post("/documents", headers=headers(), json=body("Changed")).status_code == 409
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0] == 1
    for value in (
        "Private recovery",
        "Private runbook title",
        "private-source-label",
        "content_b64",
    ):
        assert value not in str(first)


def test_background_publication_uses_only_ids_and_durable_status(api):
    client, store, publish = api
    response = client.post("/documents?background=true", headers=headers(), json=body())
    assert response.status_code == 202, response.text
    job = response.json()["job"]
    publish.assert_called_once_with(
        args=[{"job_id": job["job_id"], "tenant_id": "tenant_a"}],
        task_id=job["job_id"],
        queue=tenant_queue("tenant_a"),
        expires=settings.TASK_QUEUE_TTL,
        retry=False,
    )
    assert deliver(job["job_id"])["state"] == "succeeded"
    assert deliver(job["job_id"])["state"] == "succeeded"
    status = client.get("/documents/jobs/" + job["job_id"], headers=headers())
    assert status.status_code == 200
    assert status.json()["state"] == "succeeded" and status.json()["attempts"] == 1
    assert client.get("/documents/" + job["document_id"], headers=headers()).status_code == 200


def test_uncertain_publication_retry_keeps_same_job(api):
    client, store, publish = api
    publish.side_effect = RuntimeError("private-broker-url")
    response = client.post("/documents?background=true", headers=headers(), json=body())
    assert response.status_code == 503 and "private-broker-url" not in response.text
    with sqlite3.connect(store.path) as db:
        original_id = db.execute("SELECT id FROM document_jobs").fetchone()[0]
    publish.side_effect = None
    replay = client.post("/documents?background=true", headers=headers(), json=body())
    assert replay.status_code == 202
    assert replay.json()["job"]["job_id"] == original_id and replay.json()["replayed"]
    assert deliver(original_id)["state"] == "succeeded"


def test_worker_rechecks_grants_and_explicit_retry_requires_both_permissions(api, monkeypatch):
    client, store, publish = api
    job_id = client.post("/documents?background=true", headers=headers(), json=body()).json()[
        "job"
    ]["job_id"]
    monkeypatch.setattr(settings, "TENANT_PERMISSIONS", {"tenant_a": frozenset()})
    assert deliver(job_id)["state"] == "failed"
    assert store.get_job(job_id, "tenant_a").error_code == "PermissionDenied"
    monkeypatch.setattr(
        settings, "TENANT_PERMISSIONS", {"tenant_a": frozenset({"document.job.retry"})}
    )
    assert client.post(f"/documents/jobs/{job_id}/retry", headers=headers()).status_code == 403
    monkeypatch.setattr(settings, "TENANT_PERMISSIONS", {"tenant_a": GRANTS})
    assert client.post(f"/documents/jobs/{job_id}/retry", headers=headers()).status_code == 202
    assert deliver(job_id)["state"] == "succeeded"
    assert store.get_job(job_id, "tenant_a").attempts == 2
    assert client.post(f"/documents/jobs/{job_id}/retry", headers=headers()).status_code == 409


def test_revoked_tenant_token_also_blocks_worker_execution(api, monkeypatch):
    client, store, publish = api
    job_id = client.post("/documents?background=true", headers=headers(), json=body()).json()[
        "job"
    ]["job_id"]
    monkeypatch.setattr(settings, "TENANT_API_KEYS", {})
    assert deliver(job_id)["state"] == "failed"
    assert store.get_job(job_id, "tenant_a").error_code == "PermissionDenied"


def test_cross_tenant_lifecycle_is_unavailable(api):
    client, store, publish = api
    job = client.post("/documents", headers=headers(), json=body()).json()["job"]
    for path in ("/documents/" + job["document_id"], "/documents/jobs/" + job["job_id"]):
        assert client.get(path, headers=headers("tenant_b")).status_code == 404
    assert (
        client.delete("/documents/" + job["document_id"], headers=headers("tenant_b")).status_code
        == 404
    )
    response = client.put(
        "/documents/" + job["document_id"],
        headers=headers("tenant_b"),
        json={**body(), "expected_version": 1},
    )
    assert response.status_code == 404
    assert client.get("/documents/" + job["document_id"], headers=headers()).json()["version"] == 1


@pytest.mark.parametrize(
    "request_headers,status", [({"X-Tenant-ID": "tenant_a"}, 401), (headers(token="wrong"), 403)]
)
def test_authentication_failure_never_submits(api, request_headers, status):
    client, store, publish = api
    assert client.post("/documents", headers=request_headers, json=body()).status_code == status
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM document_jobs").fetchone()[0] == 0
    publish.assert_not_called()


def test_write_read_and_job_permissions_are_distinct(api, monkeypatch):
    client, store, publish = api
    job = client.post("/documents", headers=headers(), json=body()).json()["job"]
    monkeypatch.setattr(
        settings, "TENANT_PERMISSIONS", {"tenant_a": frozenset({"document.create"})}
    )
    assert client.get("/documents/" + job["document_id"], headers=headers()).status_code == 403
    assert client.get("/documents/jobs/" + job["job_id"], headers=headers()).status_code == 403
    assert client.delete("/documents/" + job["document_id"], headers=headers()).status_code == 403
    assert (
        client.put(
            "/documents/" + job["document_id"],
            headers=headers(key="edit"),
            json={**body(), "expected_version": 1},
        ).status_code
        == 403
    )


def test_invalid_input_and_missing_key_are_redacted(api):
    client, store, publish = api
    invalid = client.post(
        "/documents", headers=headers(), json={**body(), "tenant_id": "private-claim"}
    )
    assert invalid.status_code == 422
    assert "private-claim" not in invalid.text and "Private recovery" not in invalid.text
    no_key = headers()
    no_key.pop("Idempotency-Key")
    assert client.post("/documents", headers=no_key, json=body()).status_code == 422
    assert (
        client.post(
            "/documents",
            headers={**headers(), "Content-Type": "application/json"},
            content=json.dumps(body("\ud800")),
        ).status_code
        == 422
    )
    publish.assert_not_called()


def test_streamed_body_limit_runs_before_registration(api, monkeypatch):
    client, store, publish = api
    monkeypatch.setattr(settings, "DOCUMENT_MAX_REQUEST_BYTES", 128)
    response = client.post(
        "/documents",
        headers={**headers(), "Content-Type": "application/json"},
        content=(part for part in [b'{"text":"', b"x" * 200, b'"}']),
    )
    assert response.status_code == 413
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM document_jobs").fetchone()[0] == 0
    publish.assert_not_called()


def test_stale_update_response_preserves_history(api):
    client, store, publish = api
    doc = client.post("/documents", headers=headers(), json=body()).json()["job"]["document_id"]
    payload = {**body("Revision 2"), "expected_version": 1}
    assert (
        client.put("/documents/" + doc, headers=headers(key="edit-1"), json=payload).status_code
        == 200
    )
    assert (
        client.put("/documents/" + doc, headers=headers(key="edit-2"), json=payload).status_code
        == 409
    )
    assert client.get("/documents/" + doc, headers=headers()).json()["version"] == 2


def test_storage_initialization_failure_is_a_safe_503(api, monkeypatch):
    client, store, publish = api
    client.app.dependency_overrides.pop(document_routes.get_document_store)

    def unavailable():
        raise sqlite3.OperationalError("private-storage-path")

    monkeypatch.setattr(document_routes, "_get_document_store", unavailable)
    response = client.get("/documents/" + str(UUID(int=0, version=4)), headers=headers())
    assert response.status_code == 503
    assert "private-storage-path" not in response.text
