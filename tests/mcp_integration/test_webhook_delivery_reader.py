"""Schema and identity checks in the executable webhook path."""

from contextlib import asynccontextmanager

import pytest

from crewos.application.interfaces.webhook_delivery_status_reader import (
    WebhookResponseInvalid,
    WebhookServiceUnavailable,
)
from crewos.evaluation.mcp_integration_demo import reference_configuration
from crewos.mcp_integration.adapters.webhook_delivery_reader import (
    TOOL_NAME,
    _INPUT_SCHEMA,
    _OUTPUT_SCHEMA,
    McpWebhookDeliveryStatusReader,
)
from crewos.mcp_integration.contracts import McpToolCallResult, McpToolDescription
from crewos.mcp_integration.server_configuration import McpServerConfigurations


class Connection:
    def __init__(self, *, schema=None, result=None):
        self.schema = schema if schema is not None else _INPUT_SCHEMA
        self.result = result
        self.calls = 0
        self.closed = False

    @asynccontextmanager
    async def session(self, *args, **kwargs):
        try:
            yield self
        finally:
            self.closed = True

    async def discover_tools(self, *, timeout_seconds):
        return (
            McpToolDescription(
                name=TOOL_NAME, input_schema=self.schema, output_schema=_OUTPUT_SCHEMA
            ),
        )

    async def call_tool(self, request, *, timeout_seconds):
        self.calls += 1
        assert request.arguments == {"delivery_id": "only_a"}
        return self.result


def reader(conn, *configs):
    return McpWebhookDeliveryStatusReader(
        McpServerConfigurations.model_validate(
            {"servers": configs or (reference_configuration(),)}
        ),
        conn,
    )


async def test_schema_drift_stops_before_invocation():
    conn = Connection(schema={"type": "object"})
    with pytest.raises(WebhookResponseInvalid):
        await reader(conn).read_async(delivery_id="only_a", tenant_id="tenant_a", timeout_seconds=1)
    assert conn.calls == 0 and conn.closed


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"tenant_id": "tenant_a", "outcome": "unavailable", "delivery_id": "other"},
        {
            "tenant_id": "tenant_a",
            "outcome": "found",
            "delivery_id": "only_a",
            "status": "succeeded",
        },
        {"tenant_id": "tenant_b", "outcome": "unavailable", "delivery_id": "only_a"},
    ],
)
async def test_invalid_structured_observation_rejected(payload):
    conn = Connection(result=McpToolCallResult(is_error=False, structured_content=payload))
    with pytest.raises(WebhookResponseInvalid):
        await reader(conn).read_async(delivery_id="only_a", tenant_id="tenant_a", timeout_seconds=1)
    assert conn.calls == 1 and conn.closed


async def test_remote_error_cannot_impersonate_unavailable():
    conn = Connection(
        result=McpToolCallResult(
            is_error=True,
            structured_content={
                "tenant_id": "tenant_a",
                "outcome": "unavailable",
                "delivery_id": "only_a",
            },
        )
    )
    with pytest.raises(WebhookServiceUnavailable, match="tool failure"):
        await reader(conn).read_async(delivery_id="only_a", tenant_id="tenant_a", timeout_seconds=1)
