"""Verify configured client limits without opening a Redis connection."""

from unittest.mock import Mock, patch

import pytest
from celery.backends.redis import RedisBackend
from pydantic import ValidationError
from redis.exceptions import TimeoutError as RedisTimeoutError

from crewos.core.config import Settings, settings
from crewos.infrastructure.celery_app import celery_app
from crewos.infrastructure.task_ownership import TaskOwnershipStore, get_task_store
from crewos.infrastructure.task_status_reader import CeleryTaskStatusReader
from tests.helpers import MemoryRedis


def test_result_backend_uses_explicit_connect_and_read_timeouts():
    backend = RedisBackend(app=celery_app, url="redis://127.0.0.1:6379/1")
    options = backend.client.connection_pool.connection_kwargs
    assert options["socket_connect_timeout"] == settings.REDIS_OPERATION_TIMEOUT
    assert options["socket_timeout"] == settings.REDIS_OPERATION_TIMEOUT
    assert options["retry_on_timeout"] is False
    assert backend.always_retry is False


def test_ownership_store_uses_shared_timeout_configuration(monkeypatch):
    monkeypatch.setattr(settings, "REDIS_OPERATION_TIMEOUT", 1.25)
    get_task_store.cache_clear()
    try:
        with patch("crewos.infrastructure.task_ownership.Redis.from_url") as factory:
            get_task_store()
        factory.assert_called_once_with(
            settings.REDIS_RESULT_BACKEND,
            decode_responses=True,
            socket_connect_timeout=1.25,
            socket_timeout=1.25,
            retry_on_timeout=False,
        )
    finally:
        get_task_store.cache_clear()


@pytest.mark.parametrize("timeout", [0, -1, 31, float("nan"), float("inf")])
def test_invalid_redis_operation_timeouts_are_rejected(timeout):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, REDIS_OPERATION_TIMEOUT=timeout)


@pytest.mark.parametrize("phase", ["ownership", "backend"])
def test_redis_timeout_propagates_instead_of_becoming_a_task_state(phase):
    store = TaskOwnershipStore(MemoryRedis())
    store.register("task-123", "tenant_a")
    factory = Mock()
    error = RedisTimeoutError("storage timeout")
    if phase == "ownership":
        store.client.get = Mock(side_effect=error)
    else:
        factory.side_effect = error
    with pytest.raises(RedisTimeoutError) as caught:
        CeleryTaskStatusReader(store, factory).read(agent_task_id="task-123", tenant_id="tenant_a")
    assert caught.value is error
    if phase == "ownership":
        factory.assert_not_called()
    else:
        factory.assert_called_once_with("task-123")
