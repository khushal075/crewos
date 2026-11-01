"""Read an owned task's observed state without returning model output."""

from collections.abc import Callable
from dataclasses import asdict
import logging

from celery.result import AsyncResult
from fastapi import HTTPException

from crewos.application.dtos.agent_task_status import (
    AgentTaskFound,
    AgentTaskUnavailable,
    GetAgentTaskStatusResult,
    TaskLookupOutcome,
)
from crewos.application.interfaces.task_status_reader import TaskStatusReaderInterface
from crewos.application.services.tool_log_context import get_tool_log_context
from crewos.infrastructure.logging import get_logger
from crewos.infrastructure.celery_app import celery_app
from crewos.infrastructure.task_ownership import TaskOwnershipStore
from crewos.infrastructure.task_state import map_celery_state
from crewos.infrastructure.tool_io import bounded_result
from contextlib import nullcontext


logger = get_logger("crewos.task_status")


class CeleryTaskStatusReader(TaskStatusReaderInterface):
    def __init__(
        self,
        ownership_store: TaskOwnershipStore,
        result_factory: Callable[[str], AsyncResult] | None = None,
        event_logger: logging.Logger | None = None,
    ):
        self._custom_result_factory = result_factory is not None
        self._ownership_store = ownership_store
        self._logger = event_logger if event_logger is not None else logger
        self._result_factory = (
            result_factory
            if result_factory is not None
            else lambda task_id: AsyncResult(task_id, app=celery_app)
        )

    def read(self, *, agent_task_id: str, tenant_id: str) -> GetAgentTaskStatusResult:
        """Tenant must come from trusted context; action permission is external."""
        correlation = get_tool_log_context()
        fields = {
            "tenant_id": tenant_id,
            **(asdict(correlation) if correlation is not None else {}),
        }
        phase = "ownership"
        try:
            try:
                self._ownership_store.require_owner(agent_task_id, tenant_id)
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                self._logger.info(
                    {
                        "event": "task_ownership_checked",
                        **fields,
                        "outcome": "unavailable",
                    }
                )
                return AgentTaskUnavailable(
                    outcome=TaskLookupOutcome.UNAVAILABLE,
                    agent_task_id=agent_task_id,
                )

            self._logger.info(
                {
                    "event": "task_ownership_checked",
                    **fields,
                    "outcome": "accessible",
                }
            )
            phase = "backend"
            scope = (
                nullcontext(self._result_factory(agent_task_id))
                if self._custom_result_factory
                else bounded_result(agent_task_id, AsyncResult)
            )
            with scope as result:
                response = AgentTaskFound(
                    outcome=TaskLookupOutcome.FOUND,
                    agent_task_id=agent_task_id,
                    status=map_celery_state(result.state),
                )
            self._logger.info(
                {
                    "event": "task_status_read_completed",
                    **fields,
                    "status": response.status.value,
                }
            )
            return response
        except Exception as error:
            self._logger.error(
                {
                    "event": "task_status_read_failed",
                    **fields,
                    "phase": phase,
                    "error_type": type(error).__name__,
                }
            )
            raise
