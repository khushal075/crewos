"""Policy events explain decisions without becoming an authorization source."""

import json
from unittest.mock import Mock, patch

import pytest

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import (
    PolicyDeniedError,
    PolicyEnforcedExecutor,
    PolicyEvaluationError,
)
from crewos.application.services.tool_log_context import (
    ToolLogContext,
    get_tool_log_context,
    tool_log_context,
)
from crewos.application.dtos.model_decision import FinalAnswerDecision
from tests.test_agent_loop import setup_loop, tool_call, inputs


@pytest.mark.parametrize("allowed", [True, False])
def test_policy_event_has_decision_and_correlation_without_permission_collection(allowed):
    logger = Mock()
    executor = PolicyEnforcedExecutor(PermissionPolicyService(), logger=logger)
    operation = Mock(return_value="sensitive-output")
    context = ToolExecutionContext(
        tenant_id="tenant_a",
        permissions=["agent_task.read", "sensitive-grant"] if allowed else [],
    )
    correlation = ToolLogContext(agent_run_id="run-1", tool_call_id="call-1", step=1)
    with tool_log_context(correlation):
        if allowed:
            assert (
                executor.execute(context=context, action="agent_task.read", operation=operation)
                == "sensitive-output"
            )
        else:
            with pytest.raises(PolicyDeniedError):
                executor.execute(context=context, action="agent_task.read", operation=operation)
    record = logger.log.call_args.args[1]
    assert record["effect"] == ("allow" if allowed else "deny")
    assert record["agent_run_id"] == "run-1"
    assert record["tool_call_id"] == "call-1"
    assert record["policy_version"] == "1"
    assert "sensitive" not in json.dumps(record)
    assert get_tool_log_context() is None
    if not allowed:
        operation.assert_not_called()


def test_policy_failure_logs_only_exception_class_and_never_runs_operation():
    logger = Mock()
    policy = Mock()
    policy.evaluate.side_effect = RuntimeError("sensitive failure")
    operation = Mock()
    with pytest.raises(PolicyEvaluationError):
        PolicyEnforcedExecutor(policy, logger=logger).execute(
            context=ToolExecutionContext(tenant_id="tenant_a"),
            action="agent_task.read",
            operation=operation,
        )
    record = logger.error.call_args.args[0]
    assert record["event"] == "tool_policy_evaluation_failed"
    assert record["error_type"] == "RuntimeError"
    assert "agent_run_id" not in record
    assert "sensitive" not in json.dumps(record)
    operation.assert_not_called()


def test_nested_log_context_restores_previous_value_after_error():
    outer = ToolLogContext("outer", "call-1", 1)
    inner = ToolLogContext("inner", "call-2", 2)
    with tool_log_context(outer):
        with pytest.raises(RuntimeError):
            with tool_log_context(inner):
                assert get_tool_log_context() == inner
                raise RuntimeError("stop")
        assert get_tool_log_context() == outer
    assert get_tool_log_context() is None


def test_agent_tool_policy_event_matches_lifecycle_ids():
    loop, _, _ = setup_loop([tool_call(), FinalAnswerDecision(type="final_answer", answer="Done")])
    lifecycle = Mock()
    loop._logger = lifecycle
    with patch("crewos.core.tool_setup.logger.log") as policy_log:
        loop.run(**inputs())
    policy_event = policy_log.call_args.args[1]
    tool_event = next(
        call.args[1]
        for call in lifecycle.log.call_args_list
        if call.args[1]["event"] == "agent_tool_call_started"
    )
    assert policy_event["agent_run_id"] == tool_event["agent_run_id"]
    assert policy_event["tool_call_id"] == tool_event["tool_call_id"]
    assert policy_event["step"] == tool_event["step"]
    assert get_tool_log_context() is None
