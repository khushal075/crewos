"""Adapt an approved MCP read to the existing synchronous webhook handler."""

import asyncio
from time import monotonic

from pydantic import JsonValue, TypeAdapter, ValidationError

from crewos.application.dtos.webhook_delivery_status import (
    GetWebhookDeliveryStatusInput,
    GetWebhookDeliveryStatusResult,
)
from crewos.application.interfaces.webhook_delivery_status_reader import (
    WebhookResponseInvalid,
    WebhookServiceUnavailable,
)
from crewos.application.services.webhook_read_validation import validate_webhook_read
from crewos.mcp_integration.approved_tool_catalog import ApprovedMcpToolCatalog, McpCatalogError
from crewos.mcp_integration.connection_manager import McpConnectionManager
from crewos.mcp_integration.contracts import McpToolCallRequest, McpToolDescription
from crewos.mcp_integration.mcp_client import McpConnectionError, McpProtocolError
from crewos.mcp_integration.schema_compatibility import (
    McpSchemaMismatch,
    verify_schema_compatibility,
)
from crewos.mcp_integration.server_configuration import McpServerConfigurations

TOOL_NAME = "get_webhook_delivery_status"
_PERMISSION = "webhook_delivery.read"
_RESULT: TypeAdapter[GetWebhookDeliveryStatusResult] = TypeAdapter(GetWebhookDeliveryStatusResult)
_INPUT_SCHEMA = GetWebhookDeliveryStatusInput.model_json_schema()
_OUTPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "required": ["tenant_id", "outcome", "delivery_id"],
    "properties": {
        "tenant_id": {"type": "string"},
        "outcome": {"enum": ["found", "unavailable"]},
        "delivery_id": {"type": "string"},
        "status": {"type": "string"},
        "observed_at": {"type": "string"},
    },
    "additionalProperties": False,
}


class McpWebhookDeliveryStatusReader:
    """Called only after the existing handler's permission gate."""

    def __init__(self, servers: McpServerConfigurations, manager: McpConnectionManager):
        self._servers = McpServerConfigurations.model_validate(servers.model_dump(mode="python"))
        self._manager = manager

    def read(self, *, delivery_id: str, tenant_id: str, timeout_seconds: float):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise WebhookServiceUnavailable("MCP webhook reader requires a synchronous caller")
        return asyncio.run(
            self.read_async(
                delivery_id=delivery_id, tenant_id=tenant_id, timeout_seconds=timeout_seconds
            )
        )

    async def read_async(self, *, delivery_id: str, tenant_id: str, timeout_seconds: float):
        validate_webhook_read(
            delivery_id=delivery_id, tenant_id=tenant_id, timeout_seconds=timeout_seconds
        )
        candidates = [
            server
            for server in self._servers.servers
            if server.enabled
            and server.tenant_id == tenant_id
            and any(
                mapping.local_name == mapping.remote_name == TOOL_NAME
                and mapping.required_permission == _PERMISSION
                for mapping in server.allowed_tools
            )
        ]
        if len(candidates) != 1:
            raise WebhookServiceUnavailable("No approved MCP webhook server for this tenant")
        deadline = monotonic() + min(timeout_seconds, candidates[0].timeout_seconds)

        def remaining() -> float:
            seconds = deadline - monotonic()
            if seconds <= 0:
                raise McpConnectionError("MCP webhook operation timed out")
            return seconds

        try:
            async with self._manager.session(
                candidates[0], tenant_id=tenant_id, timeout_seconds=remaining()
            ) as client:
                discovered = await client.discover_tools(timeout_seconds=remaining())
                entry = ApprovedMcpToolCatalog(candidates[0], discovered).resolve(TOOL_NAME)
                verify_schema_compatibility(
                    entry,
                    expected=McpToolDescription(
                        name=TOOL_NAME,
                        input_schema=_INPUT_SCHEMA,
                        output_schema=_OUTPUT_SCHEMA,
                    ),
                )
                result = await client.call_tool(
                    McpToolCallRequest(
                        tool_name=entry.remote_name, arguments={"delivery_id": delivery_id}
                    ),
                    timeout_seconds=remaining(),
                )
                if result.is_error:
                    raise WebhookServiceUnavailable("MCP webhook server reported a tool failure")
                if result.structured_content is None:
                    raise WebhookResponseInvalid("MCP webhook result lacks structured content")
                payload = dict(result.structured_content)
                if payload.pop("tenant_id", None) != tenant_id:
                    raise WebhookResponseInvalid(
                        "MCP webhook result has a different tenant identity"
                    )
                observation = _RESULT.validate_python(payload)
                if observation.delivery_id != delivery_id:
                    raise WebhookResponseInvalid(
                        "MCP webhook result has a different delivery identity"
                    )
                return observation
        except McpConnectionError:
            raise WebhookServiceUnavailable("MCP webhook connection failed") from None
        except (McpProtocolError, McpCatalogError, McpSchemaMismatch, ValidationError):
            raise WebhookResponseInvalid("Invalid MCP webhook contract or response") from None
