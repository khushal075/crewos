"""Compose the opt-in MCP webhook reader; construction starts no processes."""

from crewos.core.config import settings
from crewos.mcp_integration.adapters.sdk_mcp_client import SdkMcpClientFactory
from crewos.mcp_integration.adapters.webhook_delivery_reader import McpWebhookDeliveryStatusReader
from crewos.mcp_integration.connection_manager import McpConnectionManager


def build_mcp_webhook_reader() -> McpWebhookDeliveryStatusReader:
    return McpWebhookDeliveryStatusReader(
        settings.MCP_WEBHOOK_SERVERS, McpConnectionManager(SdkMcpClientFactory())
    )
