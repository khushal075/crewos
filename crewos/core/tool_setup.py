"""Explicit construction of the currently supported, policy-protected tools."""

from crewos.application.dtos.agent_task_status import GetAgentTaskStatusInput
from crewos.application.interfaces.task_status_reader import TaskStatusReaderInterface
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.application.services.tool_dispatcher import ToolDispatcher
from crewos.application.services.tool_registry import ToolDefinition, ToolRegistry
from crewos.application.use_cases.get_agent_task_status import GetAgentTaskStatusHandler


def build_tool_dispatcher(*, reader: TaskStatusReaderInterface) -> ToolDispatcher:
    """Build a fresh registry; callers supply the reader and trusted call context.

    Construction performs no task lookup and grants no permissions. This factory
    is not automatically invoked by an endpoint or worker.
    """
    executor = PolicyEnforcedExecutor(PermissionPolicyService())
    handler = GetAgentTaskStatusHandler(executor=executor, reader=reader)
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="get_agent_task_status",
            description="Read the observed status of a task owned by the current tenant.",
            argument_schema=GetAgentTaskStatusInput,
            handler=handler.execute,
        )
    )
    return ToolDispatcher(registry)
