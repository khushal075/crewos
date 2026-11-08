"""One MCP session per execution, without shared sessions or connection pooling."""

import asyncio
import math
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Protocol

from crewos.mcp_integration.mcp_client import McpClientInterface, McpConnectionError
from crewos.mcp_integration.server_configuration import McpServerConfiguration


class McpClientFactoryInterface(Protocol):
    async def connect(
        self, configuration: McpServerConfiguration, *, timeout_seconds: float
    ) -> McpClientInterface:
        """Return an initialized client owned by the current execution.

        Must clean up partial initialization on failure/cancellation, and exclude
        secrets/remote payloads from errors. No implicit retry. The manager cannot
        close a client that the factory never returned. Implementations must not
        leave task-bound context managers open across this method's return; the
        concrete SDK adapter must explicitly design its resource ownership.
        """
        ...


class McpConnectionManager:
    def __init__(self, factory: McpClientFactoryInterface, *, cleanup_timeout_seconds: float = 5):
        if (
            isinstance(cleanup_timeout_seconds, bool)
            or not math.isfinite(cleanup_timeout_seconds)
            or not 0 < cleanup_timeout_seconds <= 30
        ):
            raise ValueError("Cleanup timeout must be finite and within (0, 30]")
        self._factory = factory
        self._cleanup_timeout = cleanup_timeout_seconds

    @asynccontextmanager
    async def session(
        self,
        configuration: McpServerConfiguration,
        *,
        tenant_id: str,
        timeout_seconds: float,
    ) -> AsyncIterator[McpClientInterface]:
        """Open for verified tenant identity; close on exit, including cancellation.

        The caller supplies tenant_id from authenticated context, not tool input.
        This checks scope, not authentication or per-tool permissions. The timeout
        bounds initialization only; each later operation needs remaining budget.
        Cleanup gets a separate bounded allowance. Cancellation during cleanup
        propagates; adapters must tolerate it. No process-kill guarantee is made
        by this generic manager for an uncooperative adapter.
        """
        config = McpServerConfiguration.model_validate(configuration.model_dump(mode="python"))
        if not config.enabled or config.tenant_id != tenant_id:
            raise McpConnectionError("MCP server is disabled or outside tenant scope")
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("MCP initialization timeout must be finite and positive")
        allowance = min(timeout_seconds, config.timeout_seconds)
        try:
            async with asyncio.timeout(allowance):
                client = await self._factory.connect(config, timeout_seconds=allowance)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise McpConnectionError("MCP session initialization failed") from None
        failed = False
        try:
            yield client
        except BaseException:
            failed = True
            raise
        finally:
            try:
                async with asyncio.timeout(self._cleanup_timeout):
                    await client.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Preserve the operation failure/cancellation rather than mask it.
                if not failed:
                    raise McpConnectionError("MCP session cleanup failed") from None
