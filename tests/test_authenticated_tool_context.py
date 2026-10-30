"""Real authentication -> configured grants -> execution context at API entry."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from crewos.api.routes import router
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.core.config import Settings, settings
from crewos.infrastructure.task_ownership import TaskOwnershipStore, get_task_store
from tests.helpers import MemoryRedis


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        settings,
        "TENANT_API_KEYS",
        {
            "tenant_a": SecretStr("token-a"),
            "tenant_b": SecretStr("token-b"),
        },
    )
    monkeypatch.setattr(
        settings,
        "TENANT_PERMISSIONS",
        {
            "tenant_a": frozenset({"agent_task.read"}),
        },
    )
    app = FastAPI()
    app.include_router(router)
    store = TaskOwnershipStore(MemoryRedis())
    app.dependency_overrides[get_task_store] = lambda: store
    return TestClient(app)


def headers(tenant="tenant_a", token="token-a"):
    return {"X-Tenant-ID": tenant, "Authorization": "Bearer " + token}


@pytest.mark.parametrize("endpoint", ["/domain/run", "/domain/run-sync"])
def test_api_context_uses_authenticated_tenant_and_server_grants(client, endpoint):
    with (
        patch("crewos.core.tenant.ToolExecutionContext", wraps=ToolExecutionContext) as context,
        patch("crewos.api.routes.run_crew"),
        patch("crewos.api.routes.CrewRunner") as runner,
    ):
        runner.run.return_value = {"output": "done"}
        response = client.post(
            endpoint,
            headers=headers(),
            json={
                "input": {
                    "message": "hello",
                    "tenant_id": "tenant_b",
                    "permissions": ["agent_task.write"],
                }
            },
        )
    assert response.status_code == 200
    context.assert_called_once_with(
        tenant_id="tenant_a",
        permissions=frozenset({"agent_task.read"}),
    )


def test_authenticated_tenant_without_grants_gets_empty_permissions(client):
    with (
        patch("crewos.core.tenant.ToolExecutionContext", wraps=ToolExecutionContext) as context,
        patch("crewos.api.routes.run_crew"),
    ):
        response = client.post(
            "/domain/run",
            headers=headers("tenant_b", "token-b"),
            json={"input": {"message": "hello"}},
        )
    assert response.status_code == 200
    context.assert_called_once_with(tenant_id="tenant_b", permissions=frozenset())


@pytest.mark.parametrize(
    "request_headers,status",
    [
        ({"X-Tenant-ID": "tenant_a"}, 401),
        (headers("tenant_b", "token-a"), 403),
    ],
)
def test_failed_authentication_never_constructs_context(client, request_headers, status):
    with (
        patch("crewos.core.tenant.ToolExecutionContext") as context,
        patch("crewos.api.routes.run_crew") as task,
    ):
        response = client.post(
            "/domain/run", headers=request_headers, json={"input": {"message": "hello"}}
        )
    assert response.status_code == status
    context.assert_not_called()
    task.apply_async.assert_not_called()


def test_permission_settings_parse_json_from_environment(monkeypatch):
    monkeypatch.setenv("TENANT_PERMISSIONS", '{"tenant_a":["agent_task.read"]}')
    configured = Settings(_env_file=None)
    assert configured.TENANT_PERMISSIONS == {"tenant_a": frozenset({"agent_task.read"})}


@pytest.mark.parametrize("permission", ["", " ", 123])
def test_bad_permission_configuration_is_rejected(permission):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, TENANT_PERMISSIONS={"tenant_a": [permission]})
