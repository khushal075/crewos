"""Application boundary for action-level policy evaluation."""

from abc import ABC, abstractmethod

from crewos.application.dtos.policy_decision import PolicyDecision
from crewos.application.dtos.tool_execution_context import ToolExecutionContext


class PolicyServiceInterface(ABC):
    @abstractmethod
    def evaluate(
        self,
        *,
        context: ToolExecutionContext,
        action: str,
    ) -> PolicyDecision:
        """Evaluate an application-selected action using trusted context.

        Callers must establish context provenance. This action-level decision
        does not replace resource ownership checks or execution enforcement.
        """
        raise NotImplementedError
