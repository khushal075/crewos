"""Opt-in real Redis + Celery routing test; no model network calls.

Set CREWOS_TEST_REDIS_URL to a dedicated Redis instance. Never use customer Redis.
"""

import os
import time
from unittest.mock import patch
import pytest


@pytest.mark.integration
def test_real_tenant_queue_and_result_ownership(monkeypatch):
    url = os.environ.get("CREWOS_TEST_REDIS_URL")
    if not url:
        pytest.skip("Set CREWOS_TEST_REDIS_URL for isolated Redis/Celery integration")
    from celery.contrib.testing.worker import start_worker
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from crewos.core.config import settings
    from crewos.core.tenant import tenant_queue
    from crewos.infrastructure.celery_app import celery_app
    from crewos.infrastructure.task_ownership import get_task_store
    from crewos.main import app

    monkeypatch.setattr(
        settings,
        "TENANT_API_KEYS",
        {"tenant_a": SecretStr("integration-a"), "tenant_b": SecretStr("integration-b")},
    )
    assert celery_app.conf.broker_url == url, "Set REDIS_BROKER_URL before importing the app"
    client = TestClient(app)

    def headers(tenant):
        return {"X-Tenant-ID": "tenant_" + tenant, "Authorization": "Bearer integration-" + tenant}

    def run(**kwargs):
        return {"output": "done for " + kwargs["tenant_id"]}

    ids = []
    try:
        with patch("crewos.workers.crew_task.CrewRunner.run", side_effect=run):
            with start_worker(
                celery_app,
                perform_ping_check=False,
                pool="solo",
                queues=[tenant_queue("tenant_a")],
                shutdown_timeout=10,
            ):
                for tenant in ["a", "b"]:
                    response = client.post(
                        "/domain/run",
                        headers=headers(tenant),
                        json={"input": {"message": "Inspect fixture incident"}},
                    )
                    assert response.status_code == 200
                    ids.append(response.json()["task_id"])

                def wait_for(task_id, tenant):
                    deadline = time.monotonic() + 12
                    while time.monotonic() < deadline:
                        response = client.get("/domain/status/" + task_id, headers=headers(tenant))
                        assert response.status_code == 200
                        data = response.json()
                        if data["status"] == "SUCCESS":
                            return data
                        assert data["status"] != "FAILURE", data
                        time.sleep(0.1)
                    pytest.fail("Worker did not finish within the test deadline")

                assert wait_for(ids[0], "a")["result"]["output"] == "done for tenant_a"
                # The A worker must not consume work from the B queue.
                assert (
                    client.get("/domain/status/" + ids[1], headers=headers("b")).json()["status"]
                    == "PENDING"
                )
                assert (
                    client.get("/domain/status/" + ids[0], headers=headers("b")).status_code == 404
                )
            # Stop A before starting B: Celery's in-process test workers share
            # an event loop. Production workers are separate processes.
            with start_worker(
                celery_app,
                perform_ping_check=False,
                pool="solo",
                queues=[tenant_queue("tenant_b")],
                shutdown_timeout=10,
            ):
                assert wait_for(ids[1], "b")["result"]["output"] == "done for tenant_b"
    finally:
        for task_id in ids:
            celery_app.AsyncResult(task_id).forget()
            get_task_store().client.delete(get_task_store().key(task_id))
