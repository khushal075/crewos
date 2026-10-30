"""Coordinate action authorization and tenant-scoped task status lookup."""

from crewos.application.dtos.agent_task_status import (
    GetAgentTaskStatusInput,
    GetAgentTaskStatusResult,
)
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.task_status_reader import TaskStatusReaderInterface
from crewos.application.services.policy_executor import PolicyEnforcedExecutor


class GetAgentTaskStatusHandler:
    def __init__(
        self,
        executor: PolicyEnforcedExecutor,
        reader: TaskStatusReaderInterface,
    ):
        self._executor = executor
        self._reader = reader

    def execute(
        self,
        *,
        arguments: GetAgentTaskStatusInput,
        context: ToolExecutionContext,
    ) -> GetAgentTaskStatusResult:
        """Accept validated arguments and context established by trusted code."""
        return self._executor.execute(
            context=context,
            action="agent_task.read",
            operation=lambda: self._reader.read(
                agent_task_id=arguments.agent_task_id,
                tenant_id=context.tenant_id,
            ),
        )
