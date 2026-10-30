"""Application-owned boundary for tenant-scoped task status access."""

from abc import ABC, abstractmethod

from crewos.application.dtos.agent_task_status import GetAgentTaskStatusResult


class TaskStatusReaderInterface(ABC):
    @abstractmethod
    def read(self, *, agent_task_id: str, tenant_id: str) -> GetAgentTaskStatusResult:
        """Check tenant ownership before reading state; propagate storage errors."""
        raise NotImplementedError
