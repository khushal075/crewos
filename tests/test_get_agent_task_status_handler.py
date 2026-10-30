"""Exercise the composed permission and resource-ownership boundaries."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from crewos.application.dtos.agent_task_status import GetAgentTaskStatusInput
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.policy_service import PolicyServiceInterface
from crewos.application.interfaces.task_status_reader import TaskStatusReaderInterface
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import (
    PolicyDeniedError,
    PolicyEnforcedExecutor,
    PolicyEvaluationError,
)
from crewos.application.use_cases.get_agent_task_status import GetAgentTaskStatusHandler
from crewos.infrastructure.task_ownership import TaskOwnershipStore
from crewos.infrastructure.task_status_reader import CeleryTaskStatusReader
from tests.helpers import MemoryRedis


def test_denial_never_calls_reader():
    reader = Mock(spec=TaskStatusReaderInterface)
    handler = GetAgentTaskStatusHandler(PolicyEnforcedExecutor(PermissionPolicyService()), reader)
    with pytest.raises(PolicyDeniedError):
        handler.execute(
            arguments=GetAgentTaskStatusInput(agent_task_id="task-123"),
            context=ToolExecutionContext(tenant_id="tenant_a"),
        )
    reader.read.assert_not_called()


def test_policy_failure_never_calls_reader():
    policy = Mock(spec=PolicyServiceInterface)
    policy.evaluate.side_effect = RuntimeError("policy unavailable")
    reader = Mock(spec=TaskStatusReaderInterface)
    handler = GetAgentTaskStatusHandler(PolicyEnforcedExecutor(policy), reader)
    with pytest.raises(PolicyEvaluationError):
        handler.execute(
            arguments=GetAgentTaskStatusInput(agent_task_id="task-123"),
            context=ToolExecutionContext(tenant_id="tenant_a"),
        )
    reader.read.assert_not_called()


@pytest.mark.parametrize("tenant,expected", [("tenant_a", "found"), ("tenant_b", "unavailable")])
def test_permission_does_not_bypass_ownership(tenant, expected):
    store = TaskOwnershipStore(MemoryRedis())
    store.register("task-123", "tenant_a")
    backend = Mock(return_value=SimpleNamespace(state="SUCCESS"))
    handler = GetAgentTaskStatusHandler(
        PolicyEnforcedExecutor(PermissionPolicyService()),
        CeleryTaskStatusReader(store, backend),
    )
    response = handler.execute(
        arguments=GetAgentTaskStatusInput(agent_task_id="task-123"),
        context=ToolExecutionContext(tenant_id=tenant, permissions=["agent_task.read"]),
    )
    assert response.outcome.value == expected
    if tenant == "tenant_a":
        backend.assert_called_once_with("task-123")
        assert response.status.value == "succeeded"
    else:
        backend.assert_not_called()


def test_reader_failure_propagates_with_trusted_tenant_scope():
    reader = Mock(spec=TaskStatusReaderInterface)
    failure = RuntimeError("storage unavailable")
    reader.read.side_effect = failure
    handler = GetAgentTaskStatusHandler(PolicyEnforcedExecutor(PermissionPolicyService()), reader)
    with pytest.raises(RuntimeError) as error:
        handler.execute(
            arguments=GetAgentTaskStatusInput(agent_task_id="task-123"),
            context=ToolExecutionContext(tenant_id="tenant_a", permissions=["agent_task.read"]),
        )
    assert error.value is failure
    reader.read.assert_called_once_with(agent_task_id="task-123", tenant_id="tenant_a")
