"""Tenant authentication and task-ownership regressions."""
from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError
from crewos.api.routes import router
from crewos.core.config import settings
from crewos.core.tenant import tenant_queue
from crewos.infrastructure.task_ownership import TaskOwnershipStore, get_task_store
from tests.helpers import MemoryRedis


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setattr(
        settings,
        "TENANT_API_KEYS",
        {
            "tenant_a": SecretStr("token-a"),
            "tenant_b": SecretStr("token-b"),
        },
    )
    store = TaskOwnershipStore(MemoryRedis())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_task_store] = lambda: store
    return TestClient(app), store



def headers(tenant="tenant_a", token="token-a"):
    return {"X-Tenant-ID": tenant, "Authorization": "Bearer " + token}



@pytest.mark.parametrize(
    "endpoint,method",
    [
        ("/domain/run", "post"),
        ("/domain/run-sync", "post"),
        ("/domain/status/secret-task", "get"),
    ],
)
def test_missing_credentials_denied_before_execution(secured, endpoint, method):
    client, _ = secured
    with (
        patch("crewos.api.routes.run_crew") as task,
        patch("crewos.api.routes.CrewRunner") as runner,
    ):
        kwargs = {"json": {"input": {"message": "hello"}}} if method == "post" else {}
        response = getattr(client, method)(endpoint, headers={"X-Tenant-ID": "tenant_a"}, **kwargs)
    assert response.status_code == 401
    task.apply_async.assert_not_called()
    runner.run.assert_not_called()



@pytest.mark.parametrize(
    "tenant,token", [("tenant_a", "wrong"), ("tenant_b", "token-a"), ("missing", "token-a")]
)
def test_token_cannot_choose_another_tenant(secured, tenant, token):
    client, _ = secured
    with patch("crewos.api.routes.run_crew") as task:
        response = client.post(
            "/domain/run", headers=headers(tenant, token), json={"input": {"message": "hello"}}
        )
    assert response.status_code == 403
    task.apply_async.assert_not_called()



def test_owner_registered_before_dispatch_and_queue_matches(secured):
    client, store = secured

    def dispatched(**kwargs):
        store.require_owner(kwargs["task_id"], "tenant_a")
        assert kwargs["queue"] == tenant_queue("tenant_a")
        assert kwargs["args"][0] == "tenant_a"
        assert kwargs["retry"] is False

    with patch("crewos.api.routes.run_crew.apply_async", side_effect=dispatched) as dispatch:
        response = client.post(
            "/domain/run", headers=headers(), json={"input": {"message": "hello"}}
        )
    assert response.status_code == 200
    assert response.json()["task_id"] == dispatch.call_args.kwargs["task_id"]



def test_cross_tenant_and_unknown_tasks_do_not_read_backend(secured):
    client, store = secured
    store.register("private-task", "tenant_b")
    with patch("crewos.api.routes.AsyncResult") as backend:
        for task_id in ["private-task", "unknown-task"]:
            response = client.get("/domain/status/" + task_id, headers=headers())
            assert response.status_code == 404
    backend.assert_not_called()



def test_owner_can_read_result(secured):
    client, store = secured
    store.register("owned", "tenant_a")
    with patch("crewos.api.routes.AsyncResult") as backend:
        backend.return_value.state = "SUCCESS"
        backend.return_value.result = {"output": "done"}
        response = client.get("/domain/status/owned", headers=headers())
    assert response.json()["result"] == {"output": "done"}



def test_storage_failure_blocks_enqueue(secured):
    client, store = secured
    with (
        patch.object(store.client, "set", side_effect=RedisConnectionError("private detail")),
        patch("crewos.api.routes.run_crew") as task,
    ):
        response = client.post(
            "/domain/run", headers=headers(), json={"input": {"message": "hello"}}
        )
    assert response.status_code == 503
    assert "private detail" not in response.text
    task.apply_async.assert_not_called()



def test_storage_failure_blocks_result_read(secured):
    client, store = secured
    with (
        patch.object(store.client, "get", side_effect=RedisConnectionError()),
        patch("crewos.api.routes.AsyncResult") as backend,
    ):
        response = client.get("/domain/status/owned", headers=headers())
    assert response.status_code == 503
    backend.assert_not_called()



def test_ambiguous_dispatch_failure_keeps_ownership(secured):
    client, store = secured
    with patch(
        "crewos.api.routes.run_crew.apply_async", side_effect=RuntimeError("publish uncertain")
    ) as task:
        response = client.post(
            "/domain/run", headers=headers(), json={"input": {"message": "hello"}}
        )
    assert response.status_code == 500
    assert "publish uncertain" not in response.text
    store.require_owner(task.call_args.kwargs["task_id"], "tenant_a")



def test_owner_registration_cannot_be_overwritten():
    store = TaskOwnershipStore(MemoryRedis())
    store.register("task", "tenant_a")
    with pytest.raises(RuntimeError):
        store.register("task", "tenant_b")
    store.require_owner("task", "tenant_a")



@pytest.mark.parametrize("tenant", ["../a", "a.b", "", "a,b", "x" * 65])
def test_unsafe_queue_names_rejected(tenant):
    with pytest.raises(ValueError):
        tenant_queue(tenant)



def test_invalid_configured_tenant_identifier_is_denied(secured, monkeypatch):
    client, _ = secured
    monkeypatch.setattr(settings, "TENANT_API_KEYS", {"a.b": SecretStr("token-a")})
    response = client.post(
        "/domain/run", headers=headers("a.b"), json={"input": {"message": "hello"}}
    )
    assert response.status_code == 403

