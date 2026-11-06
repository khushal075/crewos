"""Syntax/admission checks only; these do not authenticate or authorize a caller."""

from math import isfinite
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.dtos.webhook_delivery_status import GetWebhookDeliveryStatusInput


def validate_webhook_read(*, delivery_id, tenant_id, timeout_seconds):
    delivery_id = GetWebhookDeliveryStatusInput(delivery_id=delivery_id).delivery_id
    tenant_id = ToolExecutionContext(tenant_id=tenant_id).tenant_id
    if (
        type(timeout_seconds) not in (int, float)
        or not isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("Webhook timeout must be finite and positive")
    return delivery_id, tenant_id
