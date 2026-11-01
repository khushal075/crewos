"""Bounded recovery, resource admission, cancellation, and resumed work."""

from unittest.mock import Mock

import httpx
import pytest
from langchain_core.messages import AIMessage
from openai import APIConnectionError
from pydantic import ValidationError

from crewos.application.dtos.model_decision import FinalAnswerDecision
from crewos.application.interfaces.run_store import RunCancelled
from crewos.application.services.decision_errors import DecisionRefusedError
from crewos.application.services.execution_control import (
    ExecutionControl,
    ResourceBudgetExceeded,
    execution_scope,
    get_execution_control,
)
from crewos.third_party.llm.json_decision_adapter import JsonDecisionAdapter
from tests.test_agent_loop import setup_loop, inputs

FINAL = FinalAnswerDecision(type="final_answer", answer="Done")


def test_one_malformed_decision_can_be_repaired_within_budget():
    loop, provider, backend = setup_loop([{"type": "invalid"}, FINAL])
    loop._control_options = {"max_recoveries": 1}
    assert loop.run(**inputs()) == FINAL
    assert len(provider.calls) == 2
    assert "previous decision" in provider.calls[1][0][-1].content
    backend.assert_not_called()
    assert get_execution_control() is None


def test_recovery_is_bounded_and_never_hides_final_error():
    loop, provider, _ = setup_loop([{"type": "invalid"}] * 3)
    loop._control_options = {"max_recoveries": 1}
    with pytest.raises(ValidationError):
        loop.run(**inputs())
    assert len(provider.calls) == 2


def test_transient_inference_connection_failure_can_recover_once():
    loop, _, _ = setup_loop([])
    loop._control_options = {"max_recoveries": 1}
    provider = Mock()
    provider.decide.side_effect = [
        APIConnectionError(request=httpx.Request("POST", "http://fixture.invalid")),
        FINAL,
    ]
    loop._provider = provider
    assert loop.run(**inputs()) == FINAL
    assert provider.decide.call_count == 2


def test_refusal_is_not_retried_as_format_repair():
    loop, _, _ = setup_loop([])
    loop._control_options = {"max_recoveries": 1}
    provider = Mock()
    provider.decide.side_effect = DecisionRefusedError("refused")
    loop._provider = provider
    with pytest.raises(DecisionRefusedError):
        loop.run(**inputs())
    provider.decide.assert_called_once()


def test_cancellation_after_model_response_prevents_tool_and_answer():
    loop, provider, backend = setup_loop([FINAL])
    control = ExecutionControl()
    cancelled = [False]
    original = provider.decide

    def decide(**kwargs):
        result = original(**kwargs)
        cancelled[0] = True
        return result

    provider.decide = decide

    def guard():
        if cancelled[0]:
            raise RunCancelled("cancelled")
        return float("inf")

    control.guard = guard
    with pytest.raises(RunCancelled):
        loop.run(**inputs(), control=control)
    backend.assert_not_called()
    assert get_execution_control() is None


@pytest.mark.parametrize("kind", ["tokens", "cost"])
def test_resource_admission_blocks_before_provider_request(kind):
    model = Mock()
    control = (
        ExecutionControl(max_tokens=1)
        if kind == "tokens"
        else ExecutionControl(
            max_tokens=100000,
            max_cost_usd="0",
            input_usd_per_million="1",
            output_usd_per_million="1",
        )
    )
    with execution_scope(control):
        with pytest.raises(ResourceBudgetExceeded):
            JsonDecisionAdapter(model).decide(messages=[], tools=[], timeout_seconds=1)
    model.invoke.assert_not_called()


def test_reservations_accumulate_and_are_not_refunded_for_uncertain_inference():
    control = ExecutionControl(
        max_tokens=100, max_cost_usd="1", input_usd_per_million="2", output_usd_per_million="4"
    )
    control.reserve(10, 20)
    assert control.state["reserved_tokens"] == 30
    assert control.state["reserved_cost"] == "0.0001"
    control.reserve(30, 20)
    assert control.state["reserved_tokens"] == 80
    with pytest.raises(ResourceBudgetExceeded):
        control.reserve(21, 0)
    assert control.state["reserved_tokens"] == 80


def test_output_limit_is_passed_to_model_request():
    model = Mock()
    model.invoke.return_value = AIMessage(content='{"type":"final_answer","answer":"Done"}')
    JsonDecisionAdapter(model, max_output_tokens=128).decide(
        messages=[], tools=[], timeout_seconds=1
    )
    assert model.invoke.call_args.kwargs["max_tokens"] == 128


def test_cost_ceiling_without_prices_is_rejected():
    with pytest.raises(ValueError):
        ExecutionControl(max_cost_usd="1")


def test_reported_usage_is_recorded_and_excess_stops_future_work():
    control = ExecutionControl(max_tokens=100)
    control.reserve(10, 20)
    control.reconcile(15, 5, 10, 20)
    assert control.state["reserved_tokens"] == 35  # unused reservation is not refunded
    assert control.state["reported_input_tokens"] == 15
    with pytest.raises(ResourceBudgetExceeded):
        control.reconcile(101, 0, 0, 0)
    assert control.state["reserved_tokens"] == 136


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_tokens": 0},
        {"max_recoveries": -1},
        {"input_usd_per_million": -1, "output_usd_per_million": 1},
        {"input_usd_per_million": 1},
    ],
)
def test_invalid_resource_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        ExecutionControl(**kwargs)


def test_safe_tool_storage_error_can_be_reported_back_for_one_redecision():
    from redis.exceptions import TimeoutError as RedisTimeout
    from tests.test_agent_loop import tool_call

    loop, provider, backend = setup_loop([tool_call(), FINAL])
    loop._control_options = {"max_recoveries": 1}
    backend.side_effect = RedisTimeout("private backend detail")
    assert loop.run(**inputs()) == FINAL
    observation = provider.calls[1][0][-1].content
    assert "tool_unavailable_or_invalid_arguments" in observation
    assert "private backend detail" not in observation


def test_heartbeat_renews_active_lease_and_stops_cleanly(tmp_path):
    import threading
    from uuid import uuid4
    from crewos.infrastructure.run_store import SQLiteRunStore

    store = SQLiteRunStore(str(tmp_path / "runs.sqlite3"), lease_seconds=0.3)
    job = {"agent_run_id": str(uuid4()), "tenant_id": "a", "message": "x", "max_decisions": 5}
    store.create(job, duration_seconds=5)
    record = store.claim(job)
    control = ExecutionControl(store=store, record=record)
    renewed = threading.Event()
    original = store.guard

    def guard(*args):
        result = original(*args)
        renewed.set()
        return result

    store.guard = guard
    control.start_heartbeat()
    try:
        assert renewed.wait(timeout=2)
    finally:
        control.close()
    assert not control._heartbeat_thread.is_alive()
    assert store.get(record["id"], record["tenant"])["lease_until"] > record["lease_until"]
