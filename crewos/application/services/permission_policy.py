"""Exact permission-membership policy; resource access is checked separately."""

from crewos.application.dtos.policy_decision import PolicyDecision, PolicyEffect
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.policy_service import PolicyServiceInterface


class PermissionPolicyService(PolicyServiceInterface):
    def evaluate(
        self,
        *,
        context: ToolExecutionContext,
        action: str,
    ) -> PolicyDecision:
        if action in context.permissions:
            return PolicyDecision(
                effect=PolicyEffect.ALLOW,
                reason_code="permission_granted",
                policy_version="1",
            )
        return PolicyDecision(
            effect=PolicyEffect.DENY,
            reason_code="permission_missing",
            policy_version="1",
        )
