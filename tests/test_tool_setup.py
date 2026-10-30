"""Verify the supported tool is assembled with real access-control components."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.task_status_reader import TaskStatusReaderInterface
from crewos.application.services.policy_executor import PolicyDeniedError
from crewos.application.services.tool_registry import UnknownToolError
from crewos.core.tool_setup import build_tool_dispatcher
from crewos.infrastructure.task_ownership import TaskOwnershipStore
from crewos.infrastructure.task_status_reader import CeleryTaskStatusReader
from tests.helpers import MemoryRedis


def test_building_tools_does_not_read_tasks():
    reader = Mock(spec=TaskStatusReaderInterface)
    build_tool_dispatcher(reader=reader)
    reader.read.assert_not_called()


@pytest.mark.parametrize(
    "tenant,permissions,expected",
    [
        ("tenant_a", [], "denied"),
        ("tenant_a", ["agent_task.read"], "found"),
        ("tenant_b", ["agent_task.read"], "unavailable"),
    ],
)
def test_built_dispatcher_registers_the_protected_status_tool(tenant, permissions, expected):
    store = TaskOwnershipStore(MemoryRedis())
    store.register("task-123", "tenant_a")
    backend = Mock(return_value=SimpleNamespace(state="SUCCESS"))
    dispatcher = build_tool_dispatcher(reader=CeleryTaskStatusReader(store, backend))
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
            assert response.status.value == "succeeded"
    if expected == "found":
        backend.assert_called_once_with("task-123")
    else:
        backend.assert_not_called()


def test_setup_exposes_only_explicitly_registered_tools():
    reader = Mock(spec=TaskStatusReaderInterface)
    dispatcher = build_tool_dispatcher(reader=reader)
    with pytest.raises(UnknownToolError):
        dispatcher.dispatch(
            name="execute_python",
            arguments={},
            context=ToolExecutionContext(tenant_id="tenant_a"),
        )
    reader.read.assert_not_called()
