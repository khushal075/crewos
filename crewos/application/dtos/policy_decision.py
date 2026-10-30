"""Policy decision structure; evaluation and enforcement live elsewhere."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    effect: PolicyEffect
    reason_code: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]*$")
    ]
    policy_version: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
    ]
