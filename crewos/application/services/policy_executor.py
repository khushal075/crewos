"""Enforce an action-level policy decision before invoking application code."""

from collections.abc import Callable
from typing import TypeVar
import logging
from dataclasses import asdict

from crewos.application.dtos.policy_decision import PolicyDecision, PolicyEffect
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.policy_service import PolicyServiceInterface
from crewos.application.services.tool_log_context import get_tool_log_context

ResultT = TypeVar("ResultT")


class PolicyDeniedError(PermissionError):
    def __init__(self, decision: PolicyDecision):
        self.decision = decision
        super().__init__("Operation not permitted")


class PolicyEvaluationError(RuntimeError):
    """Policy evaluation failed; the protected operation was not executed."""


class PolicyEnforcedExecutor:
    def __init__(
        self, policy_service: PolicyServiceInterface, logger: logging.Logger | None = None
    ):
        self._policy_service = policy_service
        self._logger = logger if logger is not None else logging.getLogger(__name__)

    def execute(
        self,
        *,
        context: ToolExecutionContext,
        action: str,
        operation: Callable[[], ResultT],
    ) -> ResultT:
        correlation = get_tool_log_context()
        fields = {
            "tenant_id": context.tenant_id,
            "action": action,
            **(asdict(correlation) if correlation is not None else {}),
        }
        try:
            decision = self._policy_service.evaluate(context=context, action=action)
            if not isinstance(decision, PolicyDecision):
                raise TypeError("Policy service must return a PolicyDecision")
        except Exception as error:
            self._logger.error(
                {
                    "event": "tool_policy_evaluation_failed",
                    **fields,
                    "error_type": type(error).__name__,
                }
            )
            raise PolicyEvaluationError("Policy evaluation failed") from error

        self._logger.log(
            logging.INFO if decision.effect is PolicyEffect.ALLOW else logging.WARNING,
            {
                "event": "tool_policy_evaluated",
                **fields,
                "effect": decision.effect.value,
                "reason_code": decision.reason_code,
                "policy_version": decision.policy_version,
            },
        )
        if decision.effect is not PolicyEffect.ALLOW:
            raise PolicyDeniedError(decision)

        return operation()
