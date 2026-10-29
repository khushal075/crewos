from contextvars import ContextVar
from contextlib import contextmanager
from typing import Generator, Optional
from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from hmac import compare_digest
import re
from crewos.core.config import settings

bearer = HTTPBearer(auto_error=False)


def tenant_queue(tenant_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant_id):
        raise ValueError("Invalid tenant identifier")
    return f"{settings.CELERY_QUEUE_NAME}.{tenant_id}"


# Simple tenant context store (could be extended to thread-local if needed)
_current_tenant: ContextVar[Optional[str]] = ContextVar("current_tenant", default=None)


def get_tenant_id(
    x_tenant_id: str = Header(...),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
) -> str:
    """Authenticate a configured tenant token; a header alone grants no access."""
    if credentials is None:
        raise HTTPException(401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"})
    expected = settings.TENANT_API_KEYS.get(x_tenant_id)
    if (
        expected is None
        or not expected.get_secret_value()
        or not compare_digest(
            credentials.credentials.encode(), expected.get_secret_value().encode()
        )
    ):
        raise HTTPException(403, "Tenant access denied")
    try:
        tenant_queue(x_tenant_id)
    except ValueError:
        raise HTTPException(403, "Tenant access denied")
    return x_tenant_id


def set_tenant(tenant_id: str) -> None:
    _current_tenant.set(tenant_id)


def get_current_tenant() -> str:
    return _current_tenant.get()


@contextmanager
def tenant_context(tenant_id: str) -> Generator[None, None, None]:
    """
    Context manager to set current tenant for service execution.
    """
    token = _current_tenant.set(tenant_id)
    try:
        yield
    finally:
        _current_tenant.reset(token)
