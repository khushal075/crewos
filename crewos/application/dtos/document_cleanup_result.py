"""Safe worker outcome metadata; never expose event bodies or ownership tokens."""

from enum import Enum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, UUID4, model_validator


class CleanupOutcome(str, Enum):
    IDLE = "idle"
    COMPLETED = "completed"
    RETRY_SCHEDULED = "retry_scheduled"
    LEASE_LOST = "lease_lost"


class DocumentCleanupResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: CleanupOutcome
    event_id: UUID4 | None = None
    attempt: int | None = Field(default=None, strict=True, ge=1, le=2**31 - 1)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.outcome is CleanupOutcome.IDLE:
            if self.event_id is not None or self.attempt is not None:
                raise ValueError("Idle cleanup has no claimed event")
        elif self.event_id is None or self.attempt is None:
            raise ValueError("A processed cleanup requires event identity and attempt")
        return self
