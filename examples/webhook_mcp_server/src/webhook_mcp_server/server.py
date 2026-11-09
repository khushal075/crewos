"""Standalone read-only webhook reference service over MCP stdio."""

import asyncio
from typing import Annotated

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ConfigDict, StringConstraints

from webhook_mcp_server.config import configured_tenant
from webhook_mcp_server.delivery_service import DeliveryService


class GetWebhookDeliveryStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    delivery_id: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    ]


def build_server(service: DeliveryService) -> Server:
    server = Server("crewos-webhook-reference")
    tool = types.Tool(
        name="get_webhook_delivery_status",
        description="Read a synthetic webhook status for the process tenant.",
        inputSchema=GetWebhookDeliveryStatusInput.model_json_schema(),
        outputSchema={
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
        },
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [tool]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict) -> dict[str, str] | types.CallToolResult:
        if name != tool.name:
            return types.CallToolResult(isError=True, content=[])
        try:
            request = GetWebhookDeliveryStatusInput.model_validate(arguments)
        except ValueError:
            return types.CallToolResult(isError=True, content=[])
        return {"tenant_id": service.tenant, **service.read(request.delivery_id)}

    return server


async def serve() -> None:
    server = build_server(DeliveryService(configured_tenant()))
    async with stdio_server() as streams:
        await server.run(*streams, server.create_initialization_options())


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
