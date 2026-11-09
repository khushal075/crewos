"""SDK normalization and subprocess lifecycle checks."""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import types

from crewos.evaluation.mcp_integration_demo import reference_configuration
from crewos.mcp_integration.adapters.sdk_mcp_client import SdkMcpClient, SdkMcpClientFactory
from crewos.mcp_integration.connection_manager import McpConnectionManager
from crewos.mcp_integration.contracts import McpToolCallRequest
from crewos.mcp_integration.mcp_client import McpConnectionError, McpProtocolError
from crewos.mcp_integration.server_configuration import McpServerConfiguration


async def test_sdk_closes_actual_child_on_cancellation(tmp_path):
    pid_path = tmp_path / "pid"
    settings = reference_configuration()
    program = settings["transport"]["args"][-1]
    settings["transport"]["args"][-1] = (
        f"import os; from pathlib import Path; Path({str(pid_path)!r}).write_text(str(os.getpid())); "
        + program
    )
    configuration = McpServerConfiguration.model_validate(settings)
    manager = McpConnectionManager(SdkMcpClientFactory())
    ready = asyncio.Event()

    async def run():
        async with manager.session(configuration, tenant_id="tenant_a", timeout_seconds=5):
            ready.set()
            await asyncio.Future()

    execution = asyncio.create_task(run())
    ready_task = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait(
            {execution, ready_task}, return_when=asyncio.FIRST_COMPLETED, timeout=8
        )
        if execution in done:
            await execution
        assert ready_task in done
        pid = int(pid_path.read_text())
        os.kill(pid, 0)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        ready_task.cancel()
        execution.cancel()
        await asyncio.gather(execution, ready_task, return_exceptions=True)


async def test_partial_initialization_timeout_reaps_actual_child(tmp_path):
    marker = tmp_path / "pid"
    values = reference_configuration()
    values["transport"]["command"] = str(Path(sys.executable).absolute())
    values["transport"]["args"] = [
        "-I",
        "-c",
        (
            f"import os,time; from pathlib import Path; Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
        ),
    ]
    configuration = McpServerConfiguration.model_validate(values)
    manager = McpConnectionManager(SdkMcpClientFactory())
    with pytest.raises(McpConnectionError, match="initialization"):
        async with manager.session(configuration, tenant_id="tenant_a", timeout_seconds=0.7):
            pytest.fail("Server never initializes")
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)


@pytest.fixture
async def fake_client():
    stop = asyncio.Event()
    owner = asyncio.create_task(stop.wait())
    sdk = SimpleNamespace()
    client = SdkMcpClient(sdk, owner, stop, 5)
    try:
        yield client, sdk
    finally:
        await client.close()


def tool(name="status", output=None):
    return types.Tool(name=name, inputSchema={"type": "object"}, outputSchema=output)


async def test_pagination_is_complete_and_result_error_stays_explicit(fake_client):
    client, sdk = fake_client

    async def list_tools(*, params):
        return (
            types.ListToolsResult(tools=[tool("first")], nextCursor="page2")
            if params is None
            else types.ListToolsResult(tools=[tool()])
        )

    async def call_tool(name, arguments):
        assert (name, arguments) == ("status", {})
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text="failed")]
        )

    sdk.list_tools, sdk.call_tool = list_tools, call_tool
    assert [t.name for t in await client.discover_tools(timeout_seconds=1)] == ["first", "status"]
    result = await client.call_tool(McpToolCallRequest(tool_name="status"), timeout_seconds=1)
    assert result.is_error and result.content[0].text == "failed"


@pytest.mark.parametrize("fault", ["repeat_cursor", "duplicate", "reference", "count", "size"])
async def test_invalid_discovery_never_enables_call(fake_client, fault):
    client, sdk = fake_client

    async def list_tools(*, params):
        if fault == "repeat_cursor":
            return types.ListToolsResult(tools=[], nextCursor="again")
        if fault == "duplicate":
            return types.ListToolsResult(tools=[tool(), tool()])
        if fault == "reference":
            return types.ListToolsResult(
                tools=[tool(output={"type": "object", "$ref": "https://example.test"})]
            )
        if fault == "count":
            return types.ListToolsResult(tools=[tool(f"t{i}") for i in range(129)])
        return types.ListToolsResult(
            tools=[tool(f"t{i}", {"type": "object", "description": "x" * 60000}) for i in range(18)]
        )

    sdk.list_tools = list_tools
    with pytest.raises(McpProtocolError):
        await client.discover_tools(timeout_seconds=1)
    with pytest.raises(McpProtocolError, match="absent"):
        await client.call_tool(McpToolCallRequest(tool_name="status"), timeout_seconds=1)


async def test_timeout_and_unsupported_result_are_distinct(fake_client):
    client, sdk = fake_client

    async def list_tools(*, params):
        return types.ListToolsResult(tools=[tool()])

    async def slow(*args, **kwargs):
        await asyncio.Future()

    async def image(*args, **kwargs):
        return types.CallToolResult(
            content=[types.ImageContent(type="image", data="AA==", mimeType="image/png")]
        )

    sdk.list_tools = slow
    with pytest.raises(McpConnectionError):
        await client.discover_tools(timeout_seconds=0.01)
    sdk.list_tools, sdk.call_tool = list_tools, image
    await client.discover_tools(timeout_seconds=1)
    with pytest.raises(McpProtocolError, match="Unsupported"):
        await client.call_tool(McpToolCallRequest(tool_name="status"), timeout_seconds=1)
    await client.close()
    with pytest.raises(McpConnectionError, match="closed"):
        await client.discover_tools(timeout_seconds=1)
