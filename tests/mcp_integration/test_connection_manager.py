"""Session scope, ownership and cooperative lifecycle behavior."""

import asyncio

import pytest

from crewos.mcp_integration.connection_manager import McpConnectionManager
from crewos.mcp_integration.mcp_client import McpConnectionError
from crewos.mcp_integration.server_configuration import McpServerConfiguration


def config(**changes):
    values = dict(
        tenant_id="tenant_a",
        server_id="server",
        enabled=True,
        transport={"command": "/usr/bin/python3"},
    )
    values.update(changes)
    return McpServerConfiguration.model_validate(values)


class Client:
    def __init__(self, close_error=None, slow=False):
        self.closed = 0
        self.close_error = close_error
        self.slow = slow

    async def close(self):
        self.closed += 1
        if self.slow:
            await asyncio.Event().wait()
        if self.close_error:
            raise self.close_error


class Factory:
    def __init__(self, *, close_error=None, slow=False):
        self.calls = []
        self.clients = []
        self.close_error = close_error
        self.slow = slow

    async def connect(self, configuration, *, timeout_seconds):
        self.calls.append((configuration, timeout_seconds))
        client = Client(self.close_error, self.slow)
        self.clients.append(client)
        return client


async def test_separate_executions_own_distinct_sessions_and_cap_timeout():
    factory = Factory()
    manager = McpConnectionManager(factory)
    for _ in range(2):
        async with manager.session(config(), tenant_id="tenant_a", timeout_seconds=20) as client:
            assert client.closed == 0
        assert client.closed == 1
    assert factory.clients[0] is not factory.clients[1]
    assert [call[1] for call in factory.calls] == [5, 5]


@pytest.mark.parametrize(
    "settings,tenant", [(config(enabled=False), "tenant_a"), (config(), "tenant_b")]
)
async def test_rejected_scope_never_opens(settings, tenant):
    factory = Factory()
    with pytest.raises(McpConnectionError):
        async with McpConnectionManager(factory).session(
            settings, tenant_id=tenant, timeout_seconds=1
        ):
            pytest.fail("must not enter")
    assert not factory.calls


async def test_body_failure_preserved_even_if_cleanup_fails():
    factory = Factory(close_error=RuntimeError("private upstream data"))
    with pytest.raises(ValueError, match="operation"):
        async with McpConnectionManager(factory).session(
            config(), tenant_id="tenant_a", timeout_seconds=1
        ):
            raise ValueError("operation")
    assert factory.clients[0].closed == 1


async def test_cleanup_failure_on_success_is_sanitized():
    factory = Factory(close_error=RuntimeError("private upstream data"))
    with pytest.raises(McpConnectionError, match="^MCP session cleanup failed$"):
        async with McpConnectionManager(factory).session(
            config(), tenant_id="tenant_a", timeout_seconds=1
        ):
            pass


async def test_cancellation_inside_operation_closes_client_and_propagates():
    factory = Factory()
    started = asyncio.Event()

    async def work():
        async with McpConnectionManager(factory).session(
            config(), tenant_id="tenant_a", timeout_seconds=1
        ):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert factory.clients[0].closed == 1


async def test_cooperative_cleanup_timeout():
    factory = Factory(slow=True)
    with pytest.raises(McpConnectionError, match="cleanup"):
        async with McpConnectionManager(factory, cleanup_timeout_seconds=0.01).session(
            config(), tenant_id="tenant_a", timeout_seconds=1
        ):
            pass


async def test_initialization_timeout_cancels_factory_partial_work():
    class SlowFactory:
        cleaned = False

        async def connect(self, configuration, *, timeout_seconds):
            try:
                await asyncio.Event().wait()
            finally:
                self.cleaned = True

    factory = SlowFactory()
    with pytest.raises(McpConnectionError, match="initialization"):
        async with McpConnectionManager(factory).session(
            config(), tenant_id="tenant_a", timeout_seconds=0.01
        ):
            pytest.fail("must not enter")
    assert factory.cleaned


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
async def test_invalid_initialization_budget_never_opens(timeout):
    factory = Factory()
    with pytest.raises(ValueError):
        async with McpConnectionManager(factory).session(
            config(), tenant_id="tenant_a", timeout_seconds=timeout
        ):
            pass
    assert not factory.calls


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, 31])
def test_invalid_cleanup_budget(timeout):
    with pytest.raises(ValueError):
        McpConnectionManager(Factory(), cleanup_timeout_seconds=timeout)
