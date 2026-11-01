"""Per-invocation Redis timeouts without mutating shared connection pools."""

from contextlib import contextmanager

from celery.backends.redis import RedisBackend
from celery.result import AsyncResult
from redis import ConnectionPool, Redis

from crewos.application.services.execution_control import get_execution_control
from crewos.core.config import settings
from crewos.infrastructure.celery_app import celery_app


@contextmanager
def ownership_client(original):
    control = get_execution_control()
    if control is None or not isinstance(original, Redis):
        yield original
        return
    limit = min(control.tool_remaining(), settings.REDIS_OPERATION_TIMEOUT)
    options = dict(original.connection_pool.connection_kwargs)
    options.update(socket_timeout=limit, socket_connect_timeout=limit, retry_on_timeout=False)
    pool = ConnectionPool(connection_class=original.connection_pool.connection_class, **options)
    client = Redis(connection_pool=pool)
    try:
        yield client
    finally:
        client.close()
        pool.disconnect()


@contextmanager
def bounded_result(task_id, result_constructor=AsyncResult):
    control = get_execution_control()
    if control is None:
        yield result_constructor(task_id, app=celery_app)
        return
    limit = min(control.tool_remaining(), settings.REDIS_OPERATION_TIMEOUT)
    backend = RedisBackend(app=celery_app, url=settings.REDIS_RESULT_BACKEND)
    backend.connparams.update(
        socket_timeout=limit, socket_connect_timeout=limit, retry_on_timeout=False
    )
    try:
        yield result_constructor(task_id, app=celery_app, backend=backend)
    finally:
        backend.client.close()
        backend.client.connection_pool.disconnect()
