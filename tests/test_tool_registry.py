"""Registry lookup is exact, explicit, and separate from tool execution."""

from unittest.mock import Mock

import pytest

from crewos.application.dtos.agent_task_status import GetAgentTaskStatusInput
from crewos.application.services.tool_registry import (
    ToolDefinition,
    ToolRegistry,
    UnknownToolError,
)


def definition(name="get_agent_task_status", handler=None):
    return ToolDefinition(
        name=name,
        description="Read the observed status of a task owned by the current tenant.",
        argument_schema=GetAgentTaskStatusInput,
        handler=handler if handler is not None else Mock(),
    )


def test_lookup_returns_registered_definition_without_executing_handler():
    handler = Mock()
    tool = definition(handler=handler)
    registry = ToolRegistry()
    registry.register(tool)
    assert registry.get(tool.name) is tool
    assert registry.get(tool.name).argument_schema is GetAgentTaskStatusInput
    handler.assert_not_called()


def test_duplicate_registration_preserves_original_handler():
    registry = ToolRegistry()
    original = definition()
    registry.register(original)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition())
    assert registry.get(original.name) is original


@pytest.mark.parametrize("name", ["missing", "GET_AGENT_TASK_STATUS", " get_agent_task_status"])
def test_unknown_names_do_not_fall_back_to_another_handler(name):
    registry = ToolRegistry()
    handler = Mock()
    registry.register(definition(handler=handler))
    with pytest.raises(UnknownToolError):
        registry.get(name)
    handler.assert_not_called()


@pytest.mark.parametrize("name", ["", " ", " tool", "tool "])
def test_invalid_registration_names_are_rejected(name):
    with pytest.raises(ValueError, match="Tool name"):
        ToolRegistry().register(definition(name))


def test_registry_instances_have_independent_registrations():
    first = ToolRegistry()
    first.register(definition())
    with pytest.raises(UnknownToolError):
        ToolRegistry().get("get_agent_task_status")


def test_tool_schemas_derive_from_registrations_and_exclude_handlers():
    handler = Mock()
    registry = ToolRegistry()
    tool = definition(handler=handler)
    registry.register(tool)
    schemas = registry.tool_schemas()
    assert len(schemas) == 1
    assert schemas[0].model_dump() == {
        "name": tool.name,
        "description": tool.description,
        "parameters": GetAgentTaskStatusInput.model_json_schema(),
    }
    handler.assert_not_called()


def test_schema_snapshots_do_not_mutate_registration_or_later_exports():
    registry = ToolRegistry()
    registry.register(definition())
    first = registry.tool_schemas()
    first[0].parameters["properties"].clear()
    later = registry.tool_schemas()
    assert "agent_task_id" in later[0].parameters["properties"]
    assert "agent_task_id" in registry.get("get_agent_task_status").argument_schema.model_fields


def test_empty_registry_exports_no_tools():
    assert ToolRegistry().tool_schemas() == ()
