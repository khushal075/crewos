"""Lazy HTTP construction, invoked only after the webhook permission gate."""

from crewos.core.config import settings
from crewos.application.interfaces.webhook_delivery_status_reader import WebhookServiceUnavailable
from crewos.third_party.webhooks.http_delivery_reader import HttpWebhookDeliveryStatusReader


class ConfiguredWebhookDeliveryStatusReader:
    def read(self, *, delivery_id: str, tenant_id: str, timeout_seconds: float):
        if not settings.WEBHOOK_INTEGRATION_ENABLED:
            raise WebhookServiceUnavailable("Webhook integration is disabled")
        config = settings.WEBHOOK_HTTP_CONNECTIONS.get(tenant_id)
        if config is None:
            raise WebhookServiceUnavailable("Webhook integration is not configured for this tenant")
        return HttpWebhookDeliveryStatusReader(
            {tenant_id: config},
            max_timeout_seconds=settings.WEBHOOK_TIMEOUT_SECONDS,
            max_response_bytes=settings.WEBHOOK_MAX_RESPONSE_BYTES,
        ).read(delivery_id=delivery_id, tenant_id=tenant_id, timeout_seconds=timeout_seconds)
