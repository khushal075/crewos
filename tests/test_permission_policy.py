"""Behavior of the first policy strategy; no model or persistence required."""

import pytest

from crewos.application.dtos.policy_decision import PolicyEffect
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService


def test_empty_permission_context_is_denied():
    context = ToolExecutionContext(tenant_id="tenant_a")
    decision = PermissionPolicyService().evaluate(context=context, action="agent_task.read")
    assert decision.effect == PolicyEffect.DENY
    assert decision.reason_code == "permission_missing"
    assert decision.policy_version == "1"


@pytest.mark.parametrize("action", ["agent_task.read", "document.read"])
def test_explicit_matching_permission_is_allowed(action):
    context = ToolExecutionContext(tenant_id="tenant_a", permissions=[action])
    decision = PermissionPolicyService().evaluate(context=context, action=action)
    assert decision.effect == PolicyEffect.ALLOW
    assert decision.reason_code == "permission_granted"
    assert decision.policy_version == "1"


@pytest.mark.parametrize("action", [
    "agent_task.write", "agent_task", "AGENT_TASK.READ", " agent_task.read", "", "*",
])
def test_read_permission_does_not_grant_other_actions(action):
    context = ToolExecutionContext(tenant_id="tenant_a", permissions=["agent_task.read"])
    decision = PermissionPolicyService().evaluate(context=context, action=action)
    assert decision.effect == PolicyEffect.DENY
    assert decision.reason_code == "permission_missing"


def test_wildcard_string_has_no_special_privileges():
    context = ToolExecutionContext(tenant_id="tenant_a", permissions=["*"])
    decision = PermissionPolicyService().evaluate(context=context, action="agent_task.read")
    assert decision.effect == PolicyEffect.DENY
