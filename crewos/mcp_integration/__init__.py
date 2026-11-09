"""MCP integration contracts, client interface and trusted server configuration."""

from crewos.mcp_integration.contracts import (
    McpToolDescription,
    McpToolCallRequest,
    McpTextContent,
    McpToolCallResult,
)

from crewos.mcp_integration.mcp_client import (
    McpClientInterface,
    McpClientError,
    McpConnectionError,
    McpProtocolError,
)

from crewos.mcp_integration.server_configuration import (
    ApprovedMcpTool,
    McpStdioTransport,
    McpStreamableHttpTransport,
    McpServerConfiguration,
    McpServerConfigurations,
)

__all__ = [
    "McpClientInterface",
    "McpClientError",
    "McpConnectionError",
    "McpProtocolError",
    "McpToolDescription",
    "McpToolCallRequest",
    "McpTextContent",
    "McpToolCallResult",
    "ApprovedMcpTool",
    "McpStdioTransport",
    "McpStreamableHttpTransport",
    "McpServerConfiguration",
    "McpServerConfigurations",
]
