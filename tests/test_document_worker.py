"""Worker envelope fencing and real Celery memory-transport integration."""

from unittest.mock import patch
from uuid import uuid4
import time

from celery import Celery
from celery.contrib.testing.worker import start_worker
from celery.exceptions import Ignore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
import pytest

from crewos.api import document_routes
from crewos.core import document_setup
from crewos.core.config import settings
from crewos.core.tenant import tenant_queue
from crewos.ingestion import DocumentJobConflict, build_sqlite_document_store
from crewos.workers.document_task import run_document_job


def invoke(payload, *, delivery_id=None, routing_key=None):
    run_document_job.push_request(
        id=delivery_id or payload["job_id"],
        delivery_info={"routing_key": routing_key or tenant_queue("tenant_a")},
    )
    try:
        return run_document_job.run(payload)
    finally:
        run_document_job.pop_request()


@pytest.mark.parametrize("change", ["id", "queue", "payload"])
def test_invalid_delivery_never_calls_runner(change):
    payload = {"job_id": str(uuid4()), "tenant_id": "tenant_a"}
    if change == "payload":
        payload["permissions"] = ["document.create"]
    with patch("crewos.workers.document_task.execute_document_job") as execute:
        with pytest.raises((ValueError, ValidationError)):
            invoke(
                payload,
                delivery_id="wrong" if change == "id" else None,
                routing_key=tenant_queue("tenant_b") if change == "queue" else None,
            )
        execute.assert_not_called()


def test_active_duplicate_is_ignored_and_task_stores_no_source_result():
    payload = {"job_id": str(uuid4()), "tenant_id": "tenant_a"}
    with patch(
        "crewos.workers.document_task.execute_document_job", side_effect=DocumentJobConflict()
    ):
        with pytest.raises(Ignore):
            invoke(payload)
    assert run_document_job.ignore_result
    assert run_document_job.max_retries == 0
    assert run_document_job.reject_on_worker_lost
    assert run_document_job.soft_time_limit == 120 and run_document_job.time_limit == 150


def test_http_to_actual_celery_delivery_and_durable_document(monkeypatch, tmp_path):
    store = build_sqlite_document_store(tmp_path / "docs.sqlite3")
    monkeypatch.setattr(settings, "TENANT_API_KEYS", {"tenant_a": SecretStr("integration-token")})
    monkeypatch.setattr(
        settings,
        "TENANT_PERMISSIONS",
        {"tenant_a": frozenset({"document.create", "document.read", "document.job.read"})},
    )
    monkeypatch.setattr(document_setup, "get_document_store", lambda: store)
    transport = Celery(
        "documents-test", broker="memory://", backend="cache+memory://", set_as_current=False
    )
    transport.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"])
    delivered = transport.task(
        bind=True, name="documents-test.run", max_retries=0, resultrepr_maxsize=0
    )(run_document_job.run.__func__)
    monkeypatch.setattr(document_routes, "run_document_job", delivered)
    app = FastAPI()
    app.include_router(document_routes.router)
    app.dependency_overrides[document_routes.get_document_store] = lambda: store
    client = TestClient(app)
    headers = {
        "X-Tenant-ID": "tenant_a",
        "Authorization": "Bearer integration-token",
        "Idempotency-Key": "integration-request",
    }
    with start_worker(
        transport,
        perform_ping_check=False,
        pool="solo",
        queues=[tenant_queue("tenant_a")],
        shutdown_timeout=10,
    ):
        response = client.post(
            "/documents?background=true",
            headers=headers,
            json={"title": "CRM recovery", "text": "Check webhook delivery logs."},
        )
        assert response.status_code == 202, response.text
        job = response.json()["job"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = client.get("/documents/jobs/" + job["job_id"], headers=headers)
            if status.json()["state"] in {"succeeded", "failed"}:
                break
            time.sleep(0.05)
        assert status.json()["state"] == "succeeded", status.text
        assert (
            client.get("/documents/" + job["document_id"], headers=headers).json()["document"][
                "text"
            ]
            == "Check webhook delivery logs."
        )
        duplicate = delivered.apply_async(
            args=[{"job_id": job["job_id"], "tenant_id": "tenant_a"}],
            task_id=job["job_id"],
            queue=tenant_queue("tenant_a"),
        )
        assert duplicate.get(timeout=5, disable_sync_subtasks=False)["state"] == "succeeded"
        assert store.get_job(job["job_id"], "tenant_a").attempts == 1


def test_celery_document_failure_filter_redacts_details():
    import logging
    from crewos.infrastructure.celery_log_privacy import CrewTaskFailurePrivacyFilter

    record = logging.LogRecord(
        "celery.app.trace", logging.ERROR, __file__, 1, "private-source", (), None
    )
    record.data = {
        "name": "crewos.workers.document_task.run_document_job",
        "id": "job",
        "exc": "private-source",
        "args": "private-source",
    }
    assert CrewTaskFailurePrivacyFilter().filter(record)
    assert "private-source" not in record.getMessage()
    assert "private-source" not in str(record.data)
