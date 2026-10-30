"""Verify backend-state interpretation without a broker, worker, or model."""

import pytest

from crewos.application.dtos.agent_task_status import AgentTaskStatus
from crewos.infrastructure.task_state import map_celery_state


@pytest.mark.parametrize("backend_state,expected", [
    ("PENDING", AgentTaskStatus.PENDING),
    ("STARTED", AgentTaskStatus.RUNNING),
    ("RETRY", AgentTaskStatus.RETRYING),
    ("SUCCESS", AgentTaskStatus.SUCCEEDED),
    ("FAILURE", AgentTaskStatus.FAILED),
    ("REVOKED", AgentTaskStatus.REVOKED),
])
def test_known_celery_state_maps_to_application_state(backend_state, expected):
    assert map_celery_state(backend_state) is expected


@pytest.mark.parametrize("backend_state", ["PROGRESS", "NEW_BACKEND_STATE", "", "queued"])
def test_unrecognized_state_does_not_invent_execution_progress(backend_state):
    assert map_celery_state(backend_state) is AgentTaskStatus.UNKNOWN
