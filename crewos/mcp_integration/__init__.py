"""MCP integration contracts; transport and execution remain unimplemented."""

from crewos.mcp_integration.contracts import (
    McpToolDescription,
    McpToolCallRequest,
    McpTextContent,
    McpToolCallResult,
)

__all__ = ["McpToolDescription", "McpToolCallRequest", "McpTextContent", "McpToolCallResult"]
