"""Bounded read-only HTTP adapter for the documented CrewOS webhook service contract."""

from collections.abc import Mapping
from http.client import HTTPException
import json
from math import isfinite
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.dtos.webhook_delivery_status import (
    WebhookDeliveryId,
    DeliveryLookupOutcome,
    GetWebhookDeliveryStatusResult,
    WebhookDeliveryFound,
    WebhookDeliveryUnavailable,
    WebhookDeliveryStatus,
)
from crewos.application.interfaces.webhook_delivery_status_reader import (
    WebhookServiceUnavailable,
    WebhookResponseInvalid,
)
from crewos.application.services.webhook_read_validation import validate_webhook_read
from crewos.third_party.webhooks.http_delivery_config import HttpWebhookTenantConfig


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _DeliveryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tenant_id: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    delivery_id: WebhookDeliveryId
    state: str = Field(strict=True, min_length=1, max_length=64)
    observed_at: AwareDatetime


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Ambiguous response object")
        result[key] = value
    return result


class HttpWebhookDeliveryStatusReader:
    STATES = {
        "queued": WebhookDeliveryStatus.PENDING,
        "sending": WebhookDeliveryStatus.IN_PROGRESS,
        "retrying": WebhookDeliveryStatus.RETRY_SCHEDULED,
        "delivered": WebhookDeliveryStatus.SUCCEEDED,
        "failed": WebhookDeliveryStatus.FAILED,
        "unknown": WebhookDeliveryStatus.UNKNOWN,
    }

    def __init__(
        self,
        connections: Mapping[str, HttpWebhookTenantConfig],
        *,
        max_timeout_seconds=5,
        max_response_bytes=16_384,
    ):
        if (
            type(max_timeout_seconds) not in (int, float)
            or not isfinite(max_timeout_seconds)
            or not 0 < max_timeout_seconds <= 30
            or type(max_response_bytes) is not int
            or not 512 <= max_response_bytes <= 65_536
        ):
            raise ValueError("Invalid webhook transport limits")
        self._connections = {}
        for tenant_id, connection in connections.items():
            tenant_id = ToolExecutionContext(tenant_id=tenant_id).tenant_id
            # model_dump preserves SecretStr objects without exposing them as text.
            self._connections[tenant_id] = HttpWebhookTenantConfig.model_validate(
                connection.model_dump()
            )
        self._timeout, self._max_bytes = max_timeout_seconds, max_response_bytes
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    def read(
        self, *, delivery_id: str, tenant_id: str, timeout_seconds: float
    ) -> GetWebhookDeliveryStatusResult:
        delivery_id, tenant_id = validate_webhook_read(
            delivery_id=delivery_id, tenant_id=tenant_id, timeout_seconds=timeout_seconds
        )
        config = self._connections.get(tenant_id)
        if config is None:
            raise WebhookServiceUnavailable("Webhook integration is not configured for this tenant")
        path = f"/v1/tenants/{quote(tenant_id, safe='')}/deliveries/{quote(delivery_id, safe='')}"
        request = Request(
            config.base_url + path,
            method="GET",
            headers={
                "Authorization": "Bearer " + config.token.get_secret_value(),
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with self._opener.open(
                request, timeout=min(self._timeout, timeout_seconds)
            ) as response:
                if response.status != 200:
                    raise WebhookResponseInvalid("Unexpected webhook success status")
                if (
                    response.headers.get_content_type() != "application/json"
                    or response.headers.get("Content-Encoding", "identity").lower() != "identity"
                ):
                    raise WebhookResponseInvalid("Unsupported webhook response representation")
                raw = response.read(self._max_bytes + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            if status == 404:
                return WebhookDeliveryUnavailable(
                    outcome=DeliveryLookupOutcome.UNAVAILABLE, delivery_id=delivery_id
                )
            # Authentication failures, redirects, throttling and server errors are not no-record.
            raise WebhookServiceUnavailable(
                f"Webhook service request failed (HTTP {status})"
            ) from None
        except (URLError, OSError, HTTPException, OverflowError):
            raise WebhookServiceUnavailable("Webhook service transport failed") from None
        if len(raw) > self._max_bytes:
            raise WebhookResponseInvalid("Webhook response exceeded byte admission")
        try:
            data = _DeliveryResponse.model_validate(
                json.loads(raw, object_pairs_hook=_unique_object)
            )
        except (ValueError, TypeError, RecursionError):
            raise WebhookResponseInvalid("Webhook service returned malformed data") from None
        if data.tenant_id != tenant_id or data.delivery_id != delivery_id:
            raise WebhookResponseInvalid("Webhook response identity differs from requested scope")
        return WebhookDeliveryFound(
            outcome=DeliveryLookupOutcome.FOUND,
            delivery_id=delivery_id,
            status=self.STATES.get(data.state, WebhookDeliveryStatus.UNKNOWN),
            observed_at=data.observed_at,
        )
