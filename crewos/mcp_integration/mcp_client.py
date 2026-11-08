"""SDK-independent contract for one tenant-bound MCP session."""

from typing import Protocol

from crewos.mcp_integration.contracts import (
    McpToolCallRequest,
    McpToolCallResult,
    McpToolDescription,
)


class McpClientError(RuntimeError):
    """Integration failure; messages must exclude credentials and remote payloads."""


class McpConnectionError(McpClientError):
    """Connection, initialization, timeout or disconnection failure."""


class McpProtocolError(McpClientError):
    """Malformed, unsupported or oversized protocol data; never a missing record."""


class McpClientInterface(Protocol):
    """One session bound to trusted tenant/server configuration by its creator.

    Creation/initialization and ownership belong to the future connection manager.
    Callers cannot switch tenant, endpoint or credentials through this interface.
    Async methods allow waiting without blocking the event loop; they do not imply
    parallel execution or make synchronous SDK operations nonblocking automatically.
    This Protocol is a static typing contract, not runtime enforcement.
    """

    async def discover_tools(self, *, timeout_seconds: float) -> tuple[McpToolDescription, ...]:
        """Return a complete bounded discovery snapshot, never automatic approval.

        The adapter must handle pagination within the supplied time allowance,
        reject duplicate names and fail rather than truncate on admission limits.
        Initial CrewOS limits: 128 tools and 1 MiB of combined normalized metadata
        JSON (UTF-8, compact separators, ensure_ascii=False, allow_nan=False).
        Individual descriptions must also satisfy their contract. Advertised data
        remains untrusted; the approved-tool catalog decides what may be exposed.
        """
        ...

    async def call_tool(
        self, request: McpToolCallRequest, *, timeout_seconds: float
    ) -> McpToolCallResult:
        """Invoke once after CrewOS authorization and approved-schema validation.

        Revalidate/snapshot Python-mode request data before serialization, and
        validate normalized output. Do not silently discard unsupported content.
        A valid remote tool error returns is_error=True. Connection/protocol
        failures raise the corresponding exception rather than a success result.
        Do not automatically retry: a lost response may follow a completed action.
        """
        ...

    async def close(self) -> None:
        """Release session resources; repeated close is safe.

        Future implementations must bound cleanup and handle cancellation without
        leaking child processes/tasks. Operations after close fail with
        McpConnectionError. Never suppress cancellation as a successful result.
        """
        ...

    # For discovery/calls the adapter must reject non-finite/nonpositive timeouts
    # and cap the effective allowance by trusted configuration and caller budget.
    # It must enforce that allowance across all waits, not per pagination page.
    # These obligations require implementation tests when the adapter is added.
