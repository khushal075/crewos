from contextvars import ContextVar
from fastapi import Header, HTTPException
from typing import Optional
from contextlib import contextmanager
from typing import Generator


# Simple tenant context store (could be extended to thread-local if needed)
_current_tenant: ContextVar[Optional[str]] = ContextVar("current_tenant", default=None)


def get_tenant_id(x_tenant_id: str = Header(...)) -> str:
    """
    FastAPI dependency to extract tenant ID from header.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-Id header is required")
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
