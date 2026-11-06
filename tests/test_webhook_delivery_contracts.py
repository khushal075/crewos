"""External delivery contracts separate lookup outcomes, execution state and errors."""

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from crewos.application.dtos.webhook_delivery_status import (
    DeliveryLookupOutcome,
    GetWebhookDeliveryStatusInput,
    GetWebhookDeliveryStatusResult,
    WebhookDeliveryFound,
    WebhookDeliveryStatus,
    WebhookDeliveryUnavailable,
)
from crewos.application.interfaces.webhook_delivery_status_reader import (
    WebhookIntegrationError,
    WebhookResponseInvalid,
    WebhookServiceUnavailable,
)

ADAPTER = TypeAdapter(GetWebhookDeliveryStatusResult)


@pytest.mark.parametrize(
    "delivery_id", ["delivery_123", "evt-42:subscription.7", "550e8400-e29b-41d4-a716-446655440000"]
)
def test_provider_identifiers_are_preserved(delivery_id):
    assert GetWebhookDeliveryStatusInput(delivery_id=delivery_id).delivery_id == delivery_id


@pytest.mark.parametrize(
    "value",
    [
        "",
        " leading",
        "trailing ",
        "../secret",
        "https://example.com/x",
        "x?token=secret",
        "x\n",
        "a" * 129,
        123,
        True,
    ],
)
def test_invalid_ids_are_rejected_without_normalizing(value):
    with pytest.raises(ValidationError):
        GetWebhookDeliveryStatusInput(delivery_id=value)


@pytest.mark.parametrize(
    "claims",
    [
        {"tenant_id": "tenant_b"},
        {"permissions": ["delivery.read"]},
        {"url": "https://example.com"},
        {"token": "secret"},
        {"agent_task_id": "task_123"},
    ],
)
def test_tool_input_cannot_supply_authority_credentials_or_task_id(claims):
    with pytest.raises(ValidationError):
        GetWebhookDeliveryStatusInput(delivery_id="delivery_123", **claims)


@pytest.mark.parametrize("status", list(WebhookDeliveryStatus))
def test_found_result_round_trips_with_aware_observation(status):
    result = WebhookDeliveryFound(
        outcome=DeliveryLookupOutcome.FOUND,
        delivery_id="delivery_123",
        status=status,
        observed_at=datetime.now(timezone.utc),
    )
    assert ADAPTER.validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        result.status = WebhookDeliveryStatus.UNKNOWN


def test_unavailable_reveals_neither_status_nor_ownership():
    result = WebhookDeliveryUnavailable(outcome="unavailable", delivery_id="delivery_123")
    assert ADAPTER.validate_json(result.model_dump_json()) == result
    for extra in (
        {"status": "succeeded"},
        {"tenant_id": "tenant_b"},
        {"reason": "belongs_to_other_tenant"},
    ):
        with pytest.raises(ValidationError):
            WebhookDeliveryUnavailable(outcome="unavailable", delivery_id="delivery_123", **extra)


def test_missing_or_invented_state_and_naive_timestamp_are_rejected():
    base = dict(
        outcome="found",
        delivery_id="delivery_123",
        status="pending",
        observed_at=datetime.now(timezone.utc),
    )
    for changes in ({"status": "HTTP_200"}, {"status": 200}, {"observed_at": datetime(2026, 1, 1)}):
        with pytest.raises(ValidationError):
            ADAPTER.validate_python({**base, **changes})
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"outcome": "found", "delivery_id": "delivery_123"})
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({**base, "outcome": "service_unavailable"})


def test_service_failure_has_an_error_channel_not_a_missing_record_outcome():
    assert issubclass(WebhookServiceUnavailable, WebhookIntegrationError)
    assert issubclass(WebhookResponseInvalid, WebhookIntegrationError)
    assert not issubclass(WebhookServiceUnavailable, WebhookResponseInvalid)
