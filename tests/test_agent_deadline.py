"""Deterministic clock tests prove cooperative boundaries without sleeping."""

from unittest.mock import Mock

import pytest

from crewos.application.dtos.model_decision import FinalAnswerDecision
from crewos.application.use_cases.agent_loop import AgentDeadlineExceeded, AgentLoop
from crewos.infrastructure.agent_metrics import PrometheusAgentMetrics
from tests.test_agent_loop import inputs, setup_loop, tool_call


def deadline_loop(monkeypatch, decisions):
    clock = [0.0]
    monkeypatch.setattr("crewos.application.use_cases.agent_loop.perf_counter", lambda: clock[0])
    loop, provider, backend = setup_loop(decisions)
    loop._max_duration_seconds = 1.0
    loop._metrics = PrometheusAgentMetrics()
    loop._logger = Mock()
    return loop, provider, backend, clock


@pytest.mark.parametrize(
    "decision", [tool_call(), FinalAnswerDecision(type="final_answer", answer="Late answer")]
)
def test_late_model_decision_does_not_start_tool_or_return_final_answer(monkeypatch, decision):
    loop, provider, backend, clock = deadline_loop(monkeypatch, [decision])
    original = provider.decide

    def slow_decision(**kwargs):
        clock[0] = 1.0
        return original(**kwargs)

    provider.decide = slow_decision
    with pytest.raises(AgentDeadlineExceeded):
        loop.run(**inputs())
    backend.assert_not_called()
    assert (
        loop._metrics.registry.get_sample_value(
            "crewos_agent_operations_total",
            {"operation": "run", "outcome": "deadline"},
        )
        == 1
    )
    assert (
        loop._metrics.registry.get_sample_value(
            "crewos_agent_operations_total",
            {"operation": "model", "outcome": "success"},
        )
        == 1
    )
    assert loop._logger.log.call_args.args[1]["reason"] == "deadline"


def test_expired_setup_stops_before_first_model_call(monkeypatch):
    loop, provider, backend, clock = deadline_loop(monkeypatch, [])
    schemas = loop._dispatcher.tool_schemas

    def slow_catalog():
        clock[0] = 2
        return schemas()

    loop._dispatcher.tool_schemas = slow_catalog
    with pytest.raises(AgentDeadlineExceeded):
        loop.run(**inputs())
    assert not provider.calls
    backend.assert_not_called()
    assert loop._logger.log.call_args.args[1]["decisions"] == 0


def test_late_tool_result_stops_before_another_model_request(monkeypatch):
    loop, provider, backend, clock = deadline_loop(monkeypatch, [tool_call()])
    result = backend.return_value

    def slow_backend(*args):
        clock[0] = 2
        return result

    backend.side_effect = slow_backend
    with pytest.raises(AgentDeadlineExceeded):
        loop.run(**inputs())
    assert len(provider.calls) == 1
    backend.assert_called_once()
    assert (
        loop._metrics.registry.get_sample_value(
            "crewos_agent_operations_total",
            {"operation": "tool", "outcome": "success"},
        )
        == 1
    )


def test_answer_before_deadline_returns_normally(monkeypatch):
    final = FinalAnswerDecision(type="final_answer", answer="Done")
    loop, _, _, _ = deadline_loop(monkeypatch, [final])
    assert loop.run(**inputs()) == final


@pytest.mark.parametrize("value", [0, -1, True, "5", float("nan"), float("inf")])
def test_invalid_deadline_configuration_is_rejected(value):
    with pytest.raises(ValueError, match="finite and positive"):
        AgentLoop(provider=Mock(), dispatcher=Mock(), max_duration_seconds=value)


def test_remaining_budget_decreases_between_model_decisions(monkeypatch):
    final = FinalAnswerDecision(type="final_answer", answer="Done")
    loop, provider, _, clock = deadline_loop(monkeypatch, [tool_call(), final])
    loop._max_duration_seconds = 10
    original = provider.decide

    def advance_clock(**kwargs):
        result = original(**kwargs)
        clock[0] += 3
        return result

    provider.decide = advance_clock
    assert loop.run(**inputs()) == final
    assert provider.timeouts == [10, 7]
