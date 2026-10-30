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
