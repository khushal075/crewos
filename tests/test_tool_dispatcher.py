"""Dispatch validates arguments and preserves the handler's access controls."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from crewos.application.dtos.agent_task_status import GetAgentTaskStatusInput
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyDeniedError, PolicyEnforcedExecutor
from crewos.application.services.tool_dispatcher import ToolDispatcher
from crewos.application.services.tool_registry import ToolDefinition, ToolRegistry, UnknownToolError
from crewos.application.use_cases.get_agent_task_status import GetAgentTaskStatusHandler
from crewos.infrastructure.task_ownership import TaskOwnershipStore
from crewos.infrastructure.task_status_reader import CeleryTaskStatusReader
from tests.helpers import MemoryRedis


def dispatcher_for(handler):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="get_agent_task_status",
            description="Read an owned task's status.",
            argument_schema=GetAgentTaskStatusInput,
            handler=handler,
        )
    )
    return ToolDispatcher(registry)


def test_dispatch_validates_arguments_and_passes_context_separately():
    handler = Mock()
    context = ToolExecutionContext(tenant_id="tenant_a")
    result = dispatcher_for(handler).dispatch(
        name="get_agent_task_status",
        arguments={"agent_task_id": " task-123 "},
        context=context,
    )
    handler.assert_called_once_with(
        arguments=GetAgentTaskStatusInput(agent_task_id="task-123"),
        context=context,
    )
    assert result is handler.return_value


def test_unknown_tool_never_calls_registered_handler():
    handler = Mock()
    with pytest.raises(UnknownToolError):
        dispatcher_for(handler).dispatch(
            name="unregistered",
            arguments={},
            context=ToolExecutionContext(tenant_id="tenant_a"),
        )
    handler.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"agent_task_id": ""},
        {"agent_task_id": 123},
        {"agent_task_id": "task-123", "tenant_id": "tenant_b"},
        {"agent_task_id": "task-123", "permissions": ["agent_task.read"]},
    ],
)
def test_invalid_or_forged_arguments_never_call_handler(arguments):
    handler = Mock()
    with pytest.raises(ValidationError):
        dispatcher_for(handler).dispatch(
            name="get_agent_task_status",
            arguments=arguments,
            context=ToolExecutionContext(tenant_id="tenant_a"),
        )
    handler.assert_not_called()


@pytest.mark.parametrize(
    "tenant,permissions,expected",
    [
        ("tenant_a", [], "denied"),
        ("tenant_a", ["agent_task.read"], "found"),
        ("tenant_b", ["agent_task.read"], "unavailable"),
    ],
)
def test_dispatch_preserves_permission_and_ownership_boundaries(tenant, permissions, expected):
    store = TaskOwnershipStore(MemoryRedis())
    store.register("task-123", "tenant_a")
    backend = Mock(return_value=SimpleNamespace(state="STARTED"))
    handler = GetAgentTaskStatusHandler(
        PolicyEnforcedExecutor(PermissionPolicyService()),
        CeleryTaskStatusReader(store, backend),
    )
    dispatcher = dispatcher_for(handler.execute)
    context = ToolExecutionContext(tenant_id=tenant, permissions=permissions)
    if expected == "denied":
        with pytest.raises(PolicyDeniedError):
            dispatcher.dispatch(
                name="get_agent_task_status",
                arguments={"agent_task_id": "task-123"},
                context=context,
            )
    else:
        response = dispatcher.dispatch(
            name="get_agent_task_status",
            arguments={"agent_task_id": "task-123"},
            context=context,
        )
        assert response.outcome.value == expected
        if expected == "found":
            assert response.status.value == "running"
    if expected == "found":
        backend.assert_called_once_with("task-123")
    else:
        backend.assert_not_called()


def test_handler_errors_are_not_disguised_as_validation_errors():
    failure = RuntimeError("backend unavailable")
    handler = Mock(side_effect=failure)
    with pytest.raises(RuntimeError) as error:
        dispatcher_for(handler).dispatch(
            name="get_agent_task_status",
            arguments={"agent_task_id": "task-123"},
            context=ToolExecutionContext(tenant_id="tenant_a"),
        )
    assert error.value is failure
