"""Translate backend state observations without reading the result backend."""

from celery import states

from crewos.application.dtos.agent_task_status import AgentTaskStatus


_STATE_MAPPING = {
    states.PENDING: AgentTaskStatus.PENDING,
    states.STARTED: AgentTaskStatus.RUNNING,
    states.RETRY: AgentTaskStatus.RETRYING,
    states.SUCCESS: AgentTaskStatus.SUCCEEDED,
    states.FAILURE: AgentTaskStatus.FAILED,
    states.REVOKED: AgentTaskStatus.REVOKED,
}


def map_celery_state(state: str) -> AgentTaskStatus:
    """Unknown/custom states stay unknown; PENDING never proves queue presence."""
    return _STATE_MAPPING.get(state, AgentTaskStatus.UNKNOWN)
