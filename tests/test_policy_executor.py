"""A denied or failed policy must prevent protected code from running."""

from unittest.mock import Mock

import pytest

from crewos.application.dtos.policy_decision import PolicyEffect
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.policy_service import PolicyServiceInterface
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import (
    PolicyDeniedError,
    PolicyEnforcedExecutor,
    PolicyEvaluationError,
)


def test_missing_permission_never_invokes_operation():
    operation = Mock()
    executor = PolicyEnforcedExecutor(PermissionPolicyService())
    with pytest.raises(PolicyDeniedError) as error:
        executor.execute(
            context=ToolExecutionContext(tenant_id="tenant_a"),
            action="agent_task.read",
            operation=operation,
        )
    operation.assert_not_called()
    assert error.value.decision.effect == PolicyEffect.DENY
    assert error.value.decision.reason_code == "permission_missing"


def test_allowed_operation_runs_once_and_returns_its_result():
    context = ToolExecutionContext(tenant_id="tenant_a", permissions=["agent_task.read"])
    operation = Mock(return_value={"example": "result"})
    policy = Mock(spec=PolicyServiceInterface, wraps=PermissionPolicyService())
    result = PolicyEnforcedExecutor(policy).execute(
        context=context,
        action="agent_task.read",
        operation=operation,
    )
    policy.evaluate.assert_called_once_with(context=context, action="agent_task.read")
    operation.assert_called_once_with()
    assert result == {"example": "result"}


def test_policy_failure_does_not_execute_operation():
    policy = Mock(spec=PolicyServiceInterface)
    policy.evaluate.side_effect = RuntimeError("Policy backend unavailable")
    operation = Mock()
    with pytest.raises(PolicyEvaluationError) as error:
        PolicyEnforcedExecutor(policy).execute(
            context=ToolExecutionContext(tenant_id="tenant_a"),
            action="agent_task.read",
            operation=operation,
        )
    operation.assert_not_called()
    assert str(error.value) == "Policy evaluation failed"


@pytest.mark.parametrize("invalid_decision", [None, True, {"effect": "allow"}])
def test_malformed_policy_result_does_not_execute_operation(invalid_decision):
    policy = Mock(spec=PolicyServiceInterface)
    policy.evaluate.return_value = invalid_decision
    operation = Mock()
    with pytest.raises(PolicyEvaluationError):
        PolicyEnforcedExecutor(policy).execute(
            context=ToolExecutionContext(tenant_id="tenant_a"),
            action="agent_task.read",
            operation=operation,
        )
    operation.assert_not_called()


def test_operation_failure_is_not_misreported_as_policy_failure():
    context = ToolExecutionContext(tenant_id="tenant_a", permissions=["agent_task.read"])
    failure = ValueError("Operation failed")
    operation = Mock(side_effect=failure)
    with pytest.raises(ValueError) as error:
        PolicyEnforcedExecutor(PermissionPolicyService()).execute(
            context=context,
            action="agent_task.read",
            operation=operation,
        )
    assert error.value is failure
    operation.assert_called_once_with()
