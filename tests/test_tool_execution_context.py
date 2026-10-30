"""Context structure and immutability; these do not establish provenance."""

import pytest
from pydantic import ValidationError

from crewos.application.dtos.tool_execution_context import ToolExecutionContext


def test_context_grants_no_permissions_by_default():
    context = ToolExecutionContext(tenant_id="tenant_a")
    assert context.permissions == frozenset()


def test_context_keeps_assigned_permissions_separate_from_arguments():
    context = ToolExecutionContext(
        tenant_id="tenant_a", permissions=["agent_task.read", "agent_task.read"]
    )
    assert context.permissions == frozenset({"agent_task.read"})
    assert context.model_dump(mode="json") == {
        "tenant_id": "tenant_a", "permissions": ["agent_task.read"],
    }


def test_context_does_not_share_mutable_permission_list():
    permissions = ["agent_task.read"]
    context = ToolExecutionContext(tenant_id="tenant_a", permissions=permissions)
    permissions.append("agent_task.write")
    assert "agent_task.write" not in context.permissions


@pytest.mark.parametrize("field,value", [
    ("tenant_id", "tenant_b"), ("permissions", frozenset({"agent_task.write"})),
])
def test_context_fields_cannot_be_reassigned(field, value):
    context = ToolExecutionContext(tenant_id="tenant_a")
    with pytest.raises(ValidationError) as error:
        setattr(context, field, value)
    assert error.value.errors()[0]["type"] == "frozen_instance"


def test_permission_collection_has_no_mutating_add_method():
    context = ToolExecutionContext(tenant_id="tenant_a")
    with pytest.raises(AttributeError):
        context.permissions.add("agent_task.write")


def test_context_requires_tenant_identity():
    with pytest.raises(ValidationError) as error:
        ToolExecutionContext()
    assert error.value.errors()[0]["loc"] == ("tenant_id",)


@pytest.mark.parametrize("tenant", ["", " ", "tenant.a", "x" * 65, 123, None])
def test_context_rejects_invalid_tenant_identifiers(tenant):
    with pytest.raises(ValidationError):
        ToolExecutionContext(tenant_id=tenant)


@pytest.mark.parametrize("permission", ["", " \t", 123, None])
def test_context_rejects_blank_or_non_string_permission_names(permission):
    with pytest.raises(ValidationError):
        ToolExecutionContext(tenant_id="tenant_a", permissions=[permission])


def test_context_rejects_undeclared_fields():
    with pytest.raises(ValidationError) as error:
        ToolExecutionContext(tenant_id="tenant_a", is_admin=True)
    assert error.value.errors()[0]["type"] == "extra_forbidden"
