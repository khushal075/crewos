"""Backend-owned deletion events and worker claims, without content payloads."""

from enum import Enum
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    UUID4,
    model_validator,
)


class CleanupFailureCode(str, Enum):
    STORAGE_UNAVAILABLE = "storage_unavailable"
    CLEANUP_FAILED = "cleanup_failed"


class _DocumentLifecycleEvent(BaseModel):
    """Backend-generated content-free identity shared by lifecycle events."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(default=1, strict=True, ge=1, le=1)
    event_id: UUID4
    tenant_id: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    document_id: UUID4
    occurred_at: AwareDatetime


class DocumentDeletedEvent(_DocumentLifecycleEvent):
    event_type: Literal["document.deleted"] = "document.deleted"


class DocumentVersionCreatedEvent(_DocumentLifecycleEvent):
    event_type: Literal["document.version_created"] = "document.version_created"
    document_version: int = Field(strict=True, ge=2, le=2**63 - 1)


DocumentCleanupEvent = Annotated[
    DocumentDeletedEvent | DocumentVersionCreatedEvent, Field(discriminator="event_type")
]
_EVENT_ADAPTER: TypeAdapter[DocumentCleanupEvent] = TypeAdapter(DocumentCleanupEvent)


def parse_cleanup_event_json(value: str) -> DocumentCleanupEvent:
    return _EVENT_ADAPTER.validate_json(value)


class DocumentCleanupClaim(BaseModel):
    """Temporary right to process one event, not an application permission grant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event: DocumentCleanupEvent
    lease_token: UUID4
    attempt: int = Field(strict=True, ge=1, le=2**31 - 1)
    lease_expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_lease_chronology(self) -> Self:
        if self.lease_expires_at <= self.event.occurred_at:
            raise ValueError("Cleanup lease must expire after the event occurred")
        return self
