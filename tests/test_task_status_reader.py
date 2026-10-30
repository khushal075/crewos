"""Ownership precedes backend access; operational failures remain errors."""

from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import pytest
from fastapi import HTTPException
from redis.exceptions import ConnectionError as RedisConnectionError

from crewos.application.dtos.agent_task_status import AgentTaskStatus
from crewos.infrastructure.task_ownership import TaskOwnershipStore
from crewos.infrastructure.task_status_reader import CeleryTaskStatusReader
from tests.helpers import MemoryRedis


@pytest.fixture
def store():
    owned = TaskOwnershipStore(MemoryRedis())
    owned.register("task-123", "tenant_a")
    return owned


@pytest.mark.parametrize(
    "state,expected",
    [
        ("SUCCESS", AgentTaskStatus.SUCCEEDED),
        ("PENDING", AgentTaskStatus.PENDING),
        ("PROGRESS", AgentTaskStatus.UNKNOWN),
    ],
)
def test_owned_task_returns_only_identifier_outcome_and_mapped_state(store, state, expected):
    factory = Mock(return_value=SimpleNamespace(state=state))
    result = CeleryTaskStatusReader(store, factory).read(
        agent_task_id="task-123",
        tenant_id="tenant_a",
    )
    factory.assert_called_once_with("task-123")
    assert result.model_dump(mode="json") == {
        "outcome": "found",
        "agent_task_id": "task-123",
        "status": expected.value,
    }


@pytest.mark.parametrize(
    "task_id,tenant",
    [
        ("missing-task", "tenant_a"),
        ("task-123", "tenant_b"),
    ],
)
def test_missing_or_other_tenant_task_never_touches_result_backend(store, task_id, tenant):
    factory = Mock()
    result = CeleryTaskStatusReader(store, factory).read(
        agent_task_id=task_id,
        tenant_id=tenant,
    )
    factory.assert_not_called()
    assert result.model_dump(mode="json") == {
        "outcome": "unavailable",
        "agent_task_id": task_id,
    }


def test_ownership_storage_failure_is_not_disguised_as_unavailable(store):
    factory = Mock()
    with patch.object(store.client, "get", side_effect=RedisConnectionError("storage down")):
        with pytest.raises(RedisConnectionError):
            CeleryTaskStatusReader(store, factory).read(
                agent_task_id="task-123",
                tenant_id="tenant_a",
            )
    factory.assert_not_called()


def test_unexpected_ownership_error_is_not_disguised_as_missing(store):
    factory = Mock()
    with patch.object(store, "require_owner", side_effect=HTTPException(503, "unavailable")):
        with pytest.raises(HTTPException) as error:
            CeleryTaskStatusReader(store, factory).read(
                agent_task_id="task-123",
                tenant_id="tenant_a",
            )
    assert error.value.status_code == 503
    factory.assert_not_called()


def test_backend_state_read_failure_is_not_a_task_status(store):
    backend = Mock()
    type(backend).state = PropertyMock(side_effect=RedisConnectionError("backend down"))
    with pytest.raises(RedisConnectionError):
        CeleryTaskStatusReader(store, Mock(return_value=backend)).read(
            agent_task_id="task-123",
            tenant_id="tenant_a",
        )


def test_default_reader_uses_existing_celery_app_and_task_identifier(store):
    from crewos.infrastructure.celery_app import celery_app

    with patch("crewos.infrastructure.task_status_reader.AsyncResult") as result:
        result.return_value.state = "STARTED"
        response = CeleryTaskStatusReader(store).read(
            agent_task_id="task-123",
            tenant_id="tenant_a",
        )
    result.assert_called_once_with("task-123", app=celery_app)
    assert response.status is AgentTaskStatus.RUNNING
