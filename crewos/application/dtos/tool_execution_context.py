"""Execution metadata supplied by application code, never model arguments."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


Permission = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]


class ToolExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    permissions: frozenset[Permission] = frozenset()
