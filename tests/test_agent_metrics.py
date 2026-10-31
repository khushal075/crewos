"""Metrics count actual boundaries with bounded, non-sensitive labels."""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from crewos.application.dtos.model_decision import FinalAnswerDecision
from crewos.application.services.policy_executor import PolicyDeniedError
from crewos.application.use_cases.agent_loop import DecisionLimitExceeded
from crewos.infrastructure.agent_metrics import PrometheusAgentMetrics
from tests.test_agent_loop import setup_loop, tool_call, inputs


def sample(metrics, operation, outcome, suffix="total"):
    name = (
        "crewos_agent_operations_total"
        if suffix == "total"
        else "crewos_agent_operation_duration_seconds_" + suffix
    )
    return metrics.registry.get_sample_value(name, {"operation": operation, "outcome": outcome})


def test_loop_success_counts_run_model_and_tool_boundaries():
    metrics = PrometheusAgentMetrics()
    loop, _, _ = setup_loop([tool_call(), FinalAnswerDecision(type="final_answer", answer="Done")])
    loop._metrics = metrics
    loop.run(**inputs())
    for operation, count in [("run", 1), ("model", 2), ("tool", 1)]:
        assert sample(metrics, operation, "success") == count
        assert sample(metrics, operation, "success", "count") == count
        assert sample(metrics, operation, "success", "sum") >= 0
    text = metrics.render().decode()
    assert 'operation="tool"' in text
    for forbidden in ["tenant_a", "task-123", "agent_run_id", "tool_call_id", "permissions"]:
        assert forbidden not in text


@pytest.mark.parametrize(
    "kind,decisions,error",
    [
        ("denied", [tool_call()], PolicyDeniedError),
        ("limit", [tool_call()], DecisionLimitExceeded),
        ("malformed", [{"type": "unknown"}], ValidationError),
    ],
)
def test_failure_measurements_are_not_double_counted(kind, decisions, error):
    metrics = PrometheusAgentMetrics()
    loop, _, _ = setup_loop(decisions, max_decisions=1)
    loop._metrics = metrics
    with pytest.raises(error):
        loop.run(**inputs(permissions=() if kind == "denied" else ("agent_task.read",)))
    outcome = "decision_limit" if kind == "limit" else "error"
    assert sample(metrics, "run", outcome) == 1
    assert sample(metrics, "run", "success") is None
    if kind == "denied":
        assert sample(metrics, "tool", "error") == 1
        assert sample(metrics, "tool", "success") is None
    if kind == "malformed":
        assert sample(metrics, "model", "error") == 1
        assert sample(metrics, "model", "success") is None
        assert sample(metrics, "tool", "error") is None
    if kind == "limit":
        assert sample(metrics, "tool", "success") == 1
        assert sample(metrics, "tool", "error") is None


def test_metrics_failure_does_not_change_successful_agent_result():
    final = FinalAnswerDecision(type="final_answer", answer="Done")
    loop, _, _ = setup_loop([final])
    metrics = Mock()
    metrics.observe.side_effect = RuntimeError("private exporter detail")
    loop._metrics = metrics
    loop._logger = Mock()
    assert loop.run(**inputs()) == final
    records = [call.args[1] for call in loop._logger.log.call_args_list]
    assert sum(record["event"] == "agent_metrics_recording_failed" for record in records) == 2
    assert "private exporter detail" not in str(records)


@pytest.mark.parametrize(
    "operation,outcome,duration",
    [
        ("private-operation", "success", 1),
        ("run", "private-outcome", 1),
        ("tool", "decision_limit", 1),
        ("run", "success", -1),
        ("run", "success", float("nan")),
        ("run", "success", float("inf")),
    ],
)
def test_invalid_labels_or_durations_are_rejected_before_counting(operation, outcome, duration):
    metrics = PrometheusAgentMetrics()
    with pytest.raises(ValueError):
        metrics.observe(operation=operation, outcome=outcome, duration_seconds=duration)
    assert sample(metrics, "run", "success") is None


def test_independent_collectors_do_not_share_counts():
    first, second = PrometheusAgentMetrics(), PrometheusAgentMetrics()
    first.observe(operation="run", outcome="success", duration_seconds=1.5)
    assert sample(first, "run", "success") == 1
    assert sample(second, "run", "success") is None
    assert sample(first, "run", "success", "sum") == 1.5
