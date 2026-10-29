"""Fail-closed task ownership checks, persisted independently of Celery results.

Redis must be private and persisted. This is tenant-level ownership, not user RBAC.
"""

from functools import lru_cache
from hashlib import sha256
from redis import Redis
from fastapi import HTTPException
from crewos.core.config import settings


class TaskOwnershipStore:
    def __init__(self, client):
        self.client = client

    @staticmethod
    def key(task_id):
        return "crewos:task-owner:" + sha256(task_id.encode()).hexdigest()

    def register(self, task_id, tenant_id):
        created = self.client.set(self.key(task_id), tenant_id, ex=settings.TASK_OWNER_TTL, nx=True)
        if not created:
            raise RuntimeError("Task identifier already registered")

    def require_owner(self, task_id, tenant_id):
        owner = self.client.get(self.key(task_id))
        if owner != tenant_id:
            raise HTTPException(404, "Task not found")


@lru_cache
def get_task_store():
    return TaskOwnershipStore(
        Redis.from_url(
            settings.REDIS_RESULT_BACKEND,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    )
