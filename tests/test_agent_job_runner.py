"""Registered worker execution uses real orchestration with controlled IO."""

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError

from crewos.application.services.policy_executor import PolicyDeniedError
from crewos.application.use_cases.agent_loop import DecisionLimitExceeded
from crewos.core.agent_job_runner import execute_agent_job
from crewos.core.config import settings
from crewos.core.tenant import get_current_tenant, tenant_context
from crewos.infrastructure.task_ownership import TaskOwnershipStore
from crewos.infrastructure.celery_app import celery_app
from tests.helpers import MemoryRedis

TOOL_CALL = (
    '{"type":"tool_call","name":"get_agent_task_status","arguments":{"agent_task_id":"task-123"}}'
)


@pytest.fixture
def job_setup(monkeypatch):
    monkeypatch.setattr(
        settings, "TENANT_PERMISSIONS", {"tenant_a": frozenset({"agent_task.read"})}
    )
    job = {"agent_run_id": str(uuid4()), "tenant_id": "tenant_a", "message": "Check task-123"}
    store = TaskOwnershipStore(MemoryRedis())
    store.register(job["agent_run_id"], "tenant_a")
    store.register("task-123", "tenant_a")
    model = Mock()
    model.invoke.side_effect = [
        AIMessage(content=TOOL_CALL),
        AIMessage(content='{"type":"final_answer","answer":"Running."}'),
    ]
    with (
        patch("crewos.core.agent_job_runner.get_task_store", return_value=store),
        patch("crewos.core.agent_job_runner.OllamaLLMProvider") as provider,
        patch("crewos.infrastructure.task_status_reader.AsyncResult") as backend,
    ):
        provider.return_value.get.return_value = model
        backend.return_value = SimpleNamespace(state="STARTED")
        yield job, store, provider, model, backend


def test_registered_job_preserves_run_id_and_restores_tenant_scope(job_setup):
    job, _, _, model, backend = job_setup
    with tenant_context("previous_tenant"):
        with patch("crewos.core.agent_setup.logger.log") as lifecycle:
            result = execute_agent_job(job)
        assert get_current_tenant() == "previous_tenant"
    assert result.answer == "Running."
    assert model.invoke.call_count == 2
    backend.assert_called_once_with("task-123", app=celery_app)
    assert {call.args[1]["agent_run_id"] for call in lifecycle.call_args_list} == {
        job["agent_run_id"]
    }


@pytest.mark.parametrize("field,value", [("tenant_id", "tenant_b"), ("agent_run_id", None)])
def test_missing_or_other_tenant_registration_stops_before_model(job_setup, field, value):
    job, _, provider, _, backend = job_setup
    modified = {**job, field: str(uuid4()) if value is None else value}
    with pytest.raises(HTTPException) as error:
        execute_agent_job(modified)
    assert error.value.status_code == 404
    provider.assert_not_called()
    backend.assert_not_called()


def test_ownership_storage_failure_stops_before_model(job_setup):
    job, store, provider, _, backend = job_setup
    with patch.object(store.client, "get", side_effect=RedisConnectionError("storage unavailable")):
        with pytest.raises(RedisConnectionError):
            execute_agent_job(job)
    provider.assert_not_called()
    backend.assert_not_called()


def test_payload_cannot_supply_permissions(job_setup):
    job, _, provider, _, backend = job_setup
    with pytest.raises(ValidationError):
        execute_agent_job({**job, "permissions": ["agent_task.read"]})
    provider.assert_not_called()
    backend.assert_not_called()


def test_worker_loaded_permissions_are_used_at_execution(job_setup, monkeypatch):
    job, _, _, model, backend = job_setup
    monkeypatch.setattr(settings, "TENANT_PERMISSIONS", {})
    with tenant_context("previous_tenant"):
        with pytest.raises(PolicyDeniedError):
            execute_agent_job(job)
        assert get_current_tenant() == "previous_tenant"
    model.invoke.assert_called_once()
    backend.assert_not_called()


def test_job_decision_budget_is_enforced(job_setup):
    job, _, _, model, _ = job_setup
    with pytest.raises(DecisionLimitExceeded):
        execute_agent_job({**job, "max_decisions": 1})
    model.invoke.assert_called_once()


def test_tool_target_ownership_remains_separate_from_run_ownership(job_setup):
    job, store, _, model, backend = job_setup
    store.client.values[store.key("task-123")] = "tenant_b"
    model.invoke.side_effect = [
        AIMessage(content=TOOL_CALL),
        AIMessage(content='{"type":"final_answer","answer":"Unavailable."}'),
    ]
    assert execute_agent_job(job).answer == "Unavailable."
    backend.assert_not_called()
