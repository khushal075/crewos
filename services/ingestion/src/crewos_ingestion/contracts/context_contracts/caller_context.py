"""
Verified caller information supplied to ingestion by trusted application code.
"""

from typing import Annotated
from pydantic import BaseModel, ConfigDict, StringConstraints

TenantId = Annotated[
    str,
    StringConstraints(strict=True, pattern="^[A-Za-z0-9_-]{1,64}$"),
]

Permission = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128),
]

class CallerContext(BaseModel):
    """
    Identify the tenant and carry the permission assigned to the caller.
    """
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)
    tenant_id: TenantId
    permissions: frozenset[Permission] = frozenset()
