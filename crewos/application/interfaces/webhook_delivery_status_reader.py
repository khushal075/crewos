"""Provider-independent, tenant-scoped read-only webhook integration boundary."""

from typing import Protocol

from crewos.application.dtos.webhook_delivery_status import GetWebhookDeliveryStatusResult


class WebhookIntegrationError(RuntimeError):
    """Integration failure; adapters must exclude secrets and upstream bodies."""


class WebhookServiceUnavailable(WebhookIntegrationError):
    """Transport, timeout, throttling or upstream service failure; not a missing record."""


class WebhookResponseInvalid(WebhookIntegrationError):
    """The service returned malformed, inconsistent or untrusted result data."""


class WebhookDeliveryStatusReaderInterface(Protocol):
    def read(
        self, *, delivery_id: str, tenant_id: str, timeout_seconds: float
    ) -> GetWebhookDeliveryStatusResult:
        """Read a logical external delivery within verified tenant scope.

        The handler must authorize the operation before invoking this boundary.
        Adapters validate identity and a finite positive timeout, scope the lookup
        before exposing state, and honor timeout_seconds as an upper transport
        allowance. A transport timeout is not an absolute execution deadline.

        Missing/other-tenant records return the same unavailable shape. Service
        failures raise WebhookServiceUnavailable; malformed responses or identity
        mismatches raise WebhookResponseInvalid. Never disguise these as no record.
        Successful output must preserve the requested ID and use a timezone-aware
        observation timestamp. Unknown upstream states require explicit mapping;
        they must not be guessed from an HTTP response code.

        Implementations obtain endpoints/credentials from trusted configuration,
        never from model arguments. Reading must not trigger retries of delivery,
        create an incident, or otherwise mutate the external system. This Protocol
        describes the boundary; it does not enforce authorization at runtime.
        """
        ...
