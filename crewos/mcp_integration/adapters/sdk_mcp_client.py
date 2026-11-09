"""MCP Python SDK adapter for trusted local stdio executables."""

import asyncio
import json
import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp import types
from mcp.shared.exceptions import McpError

from crewos.mcp_integration.contracts import (
    McpTextContent,
    McpToolCallRequest,
    McpToolCallResult,
    McpToolDescription,
)
from crewos.mcp_integration.mcp_client import McpConnectionError, McpProtocolError
from crewos.mcp_integration.server_configuration import McpServerConfiguration


@contextmanager
def _protocol_errors() -> Iterator[None]:
    try:
        yield
    except (McpConnectionError, McpProtocolError):
        raise
    except McpError as exc:
        if exc.error.code in (types.CONNECTION_CLOSED, 408):
            raise McpConnectionError("MCP connection operation failed") from None
        raise McpProtocolError("MCP protocol operation failed") from None
    except (ValueError, RecursionError):
        raise McpProtocolError("MCP protocol operation failed") from None
    except Exception:
        raise McpConnectionError("MCP connection operation failed") from None


def _reject_schema_references(schema: object) -> None:
    pending = [schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if {"$ref", "$dynamicRef", "$recursiveRef"} & value.keys():
                raise McpProtocolError("MCP schema references are unsupported")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


class SdkMcpClient:
    def __init__(
        self,
        session: ClientSession,
        owner: asyncio.Task[None],
        stop: asyncio.Event,
        timeout_seconds: float,
    ):
        self._session = session
        self._owner = owner
        self._stop = stop
        self._timeout = timeout_seconds
        self._discovered: frozenset[str] = frozenset()
        self._lock = asyncio.Lock()

    def _allowance(self, requested: float) -> float:
        if self._stop.is_set() or self._owner.done():
            raise McpConnectionError("MCP session is closed")
        if isinstance(requested, bool) or not math.isfinite(requested) or requested <= 0:
            raise ValueError("MCP operation timeout must be finite and positive")
        return min(requested, self._timeout)

    async def discover_tools(self, *, timeout_seconds: float) -> tuple[McpToolDescription, ...]:
        allowance = self._allowance(timeout_seconds)
        with _protocol_errors():
            async with asyncio.timeout(allowance), self._lock:
                self._discovered = frozenset()
                tools = []
                names: set[str] = set()
                cursors: set[str] = set()
                cursor = None
                size = 0
                for _ in range(128):
                    page = await self._session.list_tools(
                        params=types.PaginatedRequestParams(cursor=cursor) if cursor else None
                    )
                    for wire in page.tools:
                        tool = McpToolDescription(
                            name=wire.name,
                            description=wire.description,
                            input_schema=wire.inputSchema,
                            output_schema=wire.outputSchema,
                        )
                        _reject_schema_references(tool.output_schema)
                        size += len(
                            json.dumps(
                                tool.model_dump(mode="python"),
                                ensure_ascii=False,
                                allow_nan=False,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        )
                        if tool.name in names or len(names) >= 128 or size > 1_048_576:
                            raise McpProtocolError("Invalid or oversized MCP discovery snapshot")
                        names.add(tool.name)
                        tools.append(tool)
                    cursor = page.nextCursor
                    if cursor is None:
                        self._discovered = frozenset(names)
                        return tuple(tools)
                    if not cursor or cursor in cursors or len(cursor) > 4096:
                        raise McpProtocolError("Invalid MCP discovery cursor")
                    cursors.add(cursor)
                raise McpProtocolError("MCP discovery exceeds pagination limit")

    async def call_tool(
        self, request: McpToolCallRequest, *, timeout_seconds: float
    ) -> McpToolCallResult:
        allowance = self._allowance(timeout_seconds)
        with _protocol_errors():
            async with asyncio.timeout(allowance), self._lock:
                validated = McpToolCallRequest.model_validate(request.model_dump(mode="python"))
                if validated.tool_name not in self._discovered:
                    raise McpProtocolError("MCP tool is absent from completed discovery")
                result = await self._session.call_tool(
                    validated.tool_name, arguments=validated.arguments
                )
                if len(result.content) > 16 or any(
                    not isinstance(block, types.TextContent) for block in result.content
                ):
                    raise McpProtocolError("Unsupported MCP tool response content")
                return McpToolCallResult(
                    is_error=result.isError,
                    content=tuple(
                        McpTextContent(text=block.text)
                        for block in result.content
                        if isinstance(block, types.TextContent)
                    ),
                    structured_content=result.structuredContent,
                )

    async def close(self) -> None:
        self._stop.set()
        try:
            await asyncio.shield(self._owner)
        except asyncio.CancelledError:
            self._owner.cancel()
            raise


class SdkMcpClientFactory:
    async def connect(
        self, configuration: McpServerConfiguration, *, timeout_seconds: float
    ) -> SdkMcpClient:
        transport = configuration.transport
        params = StdioServerParameters(
            command=transport.command,
            args=list(transport.args),
            cwd=transport.cwd,
            env={name: secret.get_secret_value() for name, secret in transport.environment.items()},
        )
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[ClientSession] = loop.create_future()
        stop = asyncio.Event()
        deadline = monotonic() + timeout_seconds

        async def own_session() -> None:
            # AnyIO task groups must be entered and exited in this same task.
            try:
                with open(os.devnull, "w") as stderr:
                    async with stdio_client(params, errlog=stderr) as streams:
                        async with ClientSession(*streams) as session:
                            async with asyncio.timeout_at(
                                loop.time() + max(0, deadline - monotonic())
                            ):
                                await session.initialize()
                            if not ready.done():
                                ready.set_result(session)
                            await stop.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                if not ready.done():
                    ready.set_exception(McpConnectionError("MCP SDK initialization failed"))
                elif not ready.cancelled():
                    raise McpConnectionError("MCP SDK session failed") from None

        owner = asyncio.create_task(own_session(), name="crewos-mcp-stdio-owner")
        try:
            async with asyncio.timeout(timeout_seconds):
                session = await ready
        except BaseException:
            # Give SDK context managers a chance to finish unwinding before returning.
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
            raise
        return SdkMcpClient(session, owner, stop, configuration.timeout_seconds)
