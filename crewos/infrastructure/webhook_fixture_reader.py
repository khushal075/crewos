"""Explicit local development data; never an automatic production fallback."""

from collections.abc import Mapping
from crewos.application.dtos.webhook_delivery_status import (
    DeliveryLookupOutcome,
    GetWebhookDeliveryStatusResult,
    WebhookDeliveryFound,
    WebhookDeliveryUnavailable,
)
from crewos.application.services.webhook_read_validation import validate_webhook_read


class FixtureWebhookDeliveryStatusReader:
    def __init__(self, records: Mapping[tuple[str, str], WebhookDeliveryFound]):
        self._records = {}
        for (tenant_id, delivery_id), record in records.items():
            validate_webhook_read(delivery_id=delivery_id, tenant_id=tenant_id, timeout_seconds=1)
            record = WebhookDeliveryFound.model_validate_json(record.model_dump_json())
            if record.delivery_id != delivery_id:
                raise ValueError("Fixture key differs from delivery identity")
            self._records[(tenant_id, delivery_id)] = record

    def read(
        self, *, delivery_id: str, tenant_id: str, timeout_seconds: float
    ) -> GetWebhookDeliveryStatusResult:
        delivery_id, tenant_id = validate_webhook_read(
            delivery_id=delivery_id, tenant_id=tenant_id, timeout_seconds=timeout_seconds
        )
        record = self._records.get((tenant_id, delivery_id))
        if record is None:
            return WebhookDeliveryUnavailable(
                outcome=DeliveryLookupOutcome.UNAVAILABLE, delivery_id=delivery_id
            )
        return WebhookDeliveryFound.model_validate_json(record.model_dump_json())
