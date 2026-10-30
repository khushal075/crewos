"""Read an owned task's observed state without returning model output."""

from collections.abc import Callable

from celery.result import AsyncResult
from fastapi import HTTPException

from crewos.application.dtos.agent_task_status import (
    AgentTaskFound,
    AgentTaskUnavailable,
    GetAgentTaskStatusResult,
    TaskLookupOutcome,
)
from crewos.infrastructure.celery_app import celery_app
from crewos.infrastructure.task_ownership import TaskOwnershipStore
from crewos.infrastructure.task_state import map_celery_state


class CeleryTaskStatusReader:
    def __init__(
        self,
        ownership_store: TaskOwnershipStore,
        result_factory: Callable[[str], AsyncResult] | None = None,
    ):
        self._ownership_store = ownership_store
        self._result_factory = (
            result_factory
            if result_factory is not None
            else lambda task_id: AsyncResult(task_id, app=celery_app)
        )

    def read(self, *, agent_task_id: str, tenant_id: str) -> GetAgentTaskStatusResult:
        """Tenant must come from trusted context; action permission is external."""
        try:
            self._ownership_store.require_owner(agent_task_id, tenant_id)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            return AgentTaskUnavailable(
                outcome=TaskLookupOutcome.UNAVAILABLE,
                agent_task_id=agent_task_id,
            )

        result = self._result_factory(agent_task_id)
        return AgentTaskFound(
            outcome=TaskLookupOutcome.FOUND,
            agent_task_id=agent_task_id,
            status=map_celery_state(result.state),
        )
