"""Enforce an action-level policy decision before invoking application code."""

from collections.abc import Callable
from typing import TypeVar

from crewos.application.dtos.policy_decision import PolicyDecision, PolicyEffect
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.policy_service import PolicyServiceInterface

ResultT = TypeVar("ResultT")


class PolicyDeniedError(PermissionError):
    def __init__(self, decision: PolicyDecision):
        self.decision = decision
        super().__init__("Operation not permitted")


class PolicyEvaluationError(RuntimeError):
    """Policy evaluation failed; the protected operation was not executed."""


class PolicyEnforcedExecutor:
    def __init__(self, policy_service: PolicyServiceInterface):
        self._policy_service = policy_service

    def execute(
        self,
        *,
        context: ToolExecutionContext,
        action: str,
        operation: Callable[[], ResultT],
    ) -> ResultT:
        try:
            decision = self._policy_service.evaluate(context=context, action=action)
            if not isinstance(decision, PolicyDecision):
                raise TypeError("Policy service must return a PolicyDecision")
        except Exception as error:
            raise PolicyEvaluationError("Policy evaluation failed") from error

        if decision.effect is not PolicyEffect.ALLOW:
            raise PolicyDeniedError(decision)

        return operation()
