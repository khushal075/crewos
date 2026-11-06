"""Read-only external delivery lookup; unrelated to CrewOS/Celery task identity."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

# Opaque provider-assigned identifier, not an endpoint, task ID, or credential.
WebhookDeliveryId = Annotated[
    str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
]


class GetWebhookDeliveryStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    delivery_id: WebhookDeliveryId


class DeliveryLookupOutcome(StrEnum):
    FOUND = "found"
    UNAVAILABLE = "unavailable"


class WebhookDeliveryStatus(StrEnum):
    """Logical delivery state reported by the integration; not HTTP status codes."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class WebhookDeliveryFound(BaseModel):
    """A readable observation, not proof of downstream business processing."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal[DeliveryLookupOutcome.FOUND]
    delivery_id: WebhookDeliveryId
    status: WebhookDeliveryStatus
    observed_at: AwareDatetime


class WebhookDeliveryUnavailable(BaseModel):
    """Missing and other-tenant records are indistinguishable; no status is exposed."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    outcome: Literal[DeliveryLookupOutcome.UNAVAILABLE]
    delivery_id: WebhookDeliveryId


GetWebhookDeliveryStatusResult = Annotated[
    WebhookDeliveryFound | WebhookDeliveryUnavailable, Field(discriminator="outcome")
]
