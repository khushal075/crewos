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
from crewos.infrastructure.run_store import SQLiteRunStore
from crewos.application.interfaces.run_store import RunNotFound
from unittest.mock import ANY
import sqlite3
from tests.helpers import MemoryRedis

TOOL_CALL = (
    '{"type":"tool_call","name":"get_agent_task_status","arguments":{"agent_task_id":"task-123"}}'
)


@pytest.fixture
def job_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(
        settings, "TENANT_PERMISSIONS", {"tenant_a": frozenset({"agent_task.read"})}
    )
    job = {"agent_run_id": str(uuid4()), "tenant_id": "tenant_a", "message": "Check task-123"}
    store = TaskOwnershipStore(MemoryRedis())
    store.register(job["agent_run_id"], "tenant_a")
    store.register("task-123", "tenant_a")
    durable = SQLiteRunStore(str(tmp_path / "runs.sqlite3"))
    durable.create({**job, "max_decisions": 5}, duration_seconds=120)
    model = Mock()
    model.invoke.side_effect = [
        AIMessage(content=TOOL_CALL),
        AIMessage(content='{"type":"final_answer","answer":"Running."}'),
    ]
    with (
        patch("crewos.core.agent_job_runner.get_task_store", return_value=store),
        patch("crewos.core.agent_job_runner.get_run_store", return_value=durable),
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
    backend.assert_called_once_with("task-123", app=celery_app, backend=ANY)
    assert {call.args[1]["agent_run_id"] for call in lifecycle.call_args_list} == {
        job["agent_run_id"]
    }


@pytest.mark.parametrize("field,value", [("tenant_id", "tenant_b"), ("agent_run_id", None)])
def test_missing_or_other_tenant_registration_stops_before_model(job_setup, field, value):
    job, _, provider, _, backend = job_setup
    modified = {**job, field: str(uuid4()) if value is None else value}
    with pytest.raises(RunNotFound):
        execute_agent_job(modified)
    provider.assert_not_called()
    backend.assert_not_called()


def test_ownership_storage_failure_stops_before_model(job_setup):
    job, store, provider, _, backend = job_setup
    with patch(
        "crewos.core.agent_job_runner.get_run_store",
        side_effect=sqlite3.OperationalError("storage unavailable"),
    ):
        with pytest.raises(sqlite3.OperationalError):
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
    from crewos.core.agent_job_runner import get_run_store

    # Register a separate immutable submission with the smaller budget.
    limited = {**job, "agent_run_id": str(uuid4()), "max_decisions": 1}
    get_run_store().create(limited, duration_seconds=120)
    with pytest.raises(DecisionLimitExceeded):
        execute_agent_job(limited)
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


def test_completed_redelivery_returns_saved_result_without_repeating_model(job_setup):
    job, _, _, model, backend = job_setup
    first = execute_agent_job(job)
    second = execute_agent_job(job)
    assert second == first
    assert model.invoke.call_count == 2
    backend.assert_called_once()


def test_resume_uses_saved_tool_observation_without_repeating_completed_read(job_setup):
    job, _, _, model, backend = job_setup
    from crewos.core.agent_job_runner import get_run_store

    model.invoke.side_effect = [AIMessage(content=TOOL_CALL), RuntimeError("interrupted")]
    with pytest.raises(RuntimeError):
        execute_agent_job(job)
    store = get_run_store()
    before = store.get(job["agent_run_id"], "tenant_a")
    assert before["state"] == "failed"
    assert before["checkpoint"]["pending"] is None
    spent = before["checkpoint"]["reserved_tokens"]
    store.resume(job["agent_run_id"], "tenant_a")
    model.invoke.side_effect = [AIMessage(content='{"type":"final_answer","answer":"Recovered."}')]
    assert execute_agent_job(job).answer == "Recovered."
    backend.assert_called_once()
    after = store.get(job["agent_run_id"], "tenant_a")
    assert after["attempts"] == 2
    assert after["deadline"] == before["deadline"]
    assert after["checkpoint"]["reserved_tokens"] > spent
    assert after["checkpoint"]["decisions"] == 3


def test_model_construction_failure_releases_attempt_as_failed(job_setup):
    job, _, provider, _, _ = job_setup
    from crewos.core.agent_job_runner import get_run_store

    provider.return_value.get.side_effect = RuntimeError("construction failed")
    with pytest.raises(RuntimeError):
        execute_agent_job(job)
    assert get_run_store().get(job["agent_run_id"], "tenant_a")["state"] == "failed"


def test_cancel_request_during_inference_prevents_tool_and_persists_cancelled(job_setup):
    job, _, _, model, backend = job_setup
    from crewos.core.agent_job_runner import get_run_store
    from crewos.application.interfaces.run_store import RunCancelled

    def cancel_during_model(*args, **kwargs):
        get_run_store().cancel(job["agent_run_id"], "tenant_a")
        return AIMessage(content=TOOL_CALL)

    model.invoke.side_effect = cancel_during_model
    with pytest.raises(RunCancelled):
        execute_agent_job(job)
    assert get_run_store().get(job["agent_run_id"], "tenant_a")["state"] == "cancelled"
    backend.assert_not_called()


def test_expired_attempt_replays_only_explicitly_safe_pending_read(job_setup):
    job, _, _, model, backend = job_setup
    from crewos.core.agent_job_runner import get_run_store
    import sqlite3

    store = get_run_store()
    canonical = {**job, "max_decisions": 5}
    first = store.claim(canonical)
    store.checkpoint(
        first["id"],
        first["tenant"],
        first["lease_token"],
        {
            "history": [{"role": "user", "content": "Check task-123"}],
            "decisions": 1,
            "pending": {
                "type": "tool_call",
                "name": "get_agent_task_status",
                "arguments": {"agent_task_id": "task-123"},
            },
            "replay_safe": True,
            "reserved_tokens": 500,
        },
    )
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE agent_runs SET lease_until=0")
    model.invoke.side_effect = [
        AIMessage(content='{"type":"final_answer","answer":"Recovered read."}')
    ]
    assert execute_agent_job(job).answer == "Recovered read."
    model.invoke.assert_called_once()
    backend.assert_called_once()
    assert store.get(job["agent_run_id"], "tenant_a")["attempts"] == 2


def test_final_answer_checkpoint_can_be_finalized_without_another_inference(job_setup):
    job, _, provider, _, backend = job_setup
    from crewos.core.agent_job_runner import get_run_store

    store = get_run_store()
    first = store.claim({**job, "max_decisions": 5})
    store.checkpoint(
        first["id"],
        first["tenant"],
        first["lease_token"],
        {
            "final_result": {"type": "final_answer", "answer": "Already computed."},
        },
    )
    store.finish(first["id"], first["tenant"], first["lease_token"], state="failed")
    store.resume(first["id"], first["tenant"])
    assert execute_agent_job(job).answer == "Already computed."
    provider.assert_not_called()
    backend.assert_not_called()


def test_changed_price_configuration_fails_closed_without_leaving_active_attempt(
    job_setup, monkeypatch
):
    job, _, provider, _, _ = job_setup
    from crewos.core.agent_job_runner import get_run_store

    store = get_run_store()
    first = store.claim({**job, "max_decisions": 5})
    store.checkpoint(
        first["id"],
        first["tenant"],
        first["lease_token"],
        {
            "limits": {"max_tokens": 100000, "max_cost_usd": "1", "max_recoveries": 1},
        },
    )
    store.finish(first["id"], first["tenant"], first["lease_token"], state="failed")
    store.resume(first["id"], first["tenant"])
    monkeypatch.setattr(settings, "AGENT_INPUT_USD_PER_MILLION", None)
    monkeypatch.setattr(settings, "AGENT_OUTPUT_USD_PER_MILLION", None)
    with pytest.raises(ValueError):
        execute_agent_job(job)
    assert store.get(first["id"], first["tenant"])["state"] == "failed"
    provider.assert_not_called()


def test_terminal_storage_failure_does_not_replace_original_execution_error(job_setup):
    job, _, _, model, _ = job_setup
    from crewos.core.agent_job_runner import get_run_store
    import sqlite3

    failure = RuntimeError("original model failure")
    model.invoke.side_effect = failure
    with patch.object(
        get_run_store(), "finish", side_effect=sqlite3.OperationalError("database down")
    ):
        with pytest.raises(RuntimeError) as caught:
            execute_agent_job(job)
    assert caught.value is failure
