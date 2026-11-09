"""Real authenticated Streamable HTTP sessions using the reference tool."""

from contextlib import asynccontextmanager
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, socket
from threading import Thread
from time import monotonic, sleep

import pytest
import uvicorn
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import ValidationError
from starlette.responses import RedirectResponse, Response

from crewos.application.interfaces.webhook_delivery_status_reader import WebhookServiceUnavailable
from crewos.evaluation.mcp_integration_demo import reference_configuration
from crewos.mcp_integration.adapters.sdk_mcp_client import SdkMcpClientFactory
from crewos.mcp_integration.adapters.webhook_delivery_reader import McpWebhookDeliveryStatusReader
from crewos.mcp_integration.connection_manager import McpConnectionManager
from crewos.mcp_integration.server_configuration import (
    McpServerConfigurations,
    McpStreamableHttpTransport,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example/mcp",
        "http://localhost/mcp",
        "https://operator:token@remote.example/mcp",
        "https://remote.example/mcp?token=x",
        "https://remote.example/mcp#part",
        "https://remote.example/mc p",
        "https://remote.example:invalid/mcp",
        "https://remote.example",
        "https://remote.example\\mcp",
        "https://remote.example:0/mcp",
    ],
)
def test_invalid_http_endpoint(url):
    with pytest.raises(ValidationError):
        McpStreamableHttpTransport(kind="streamable_http", url=url, bearer_token="secret")


@pytest.mark.parametrize("token", ["", "with space", "bad\r\nHeader:value", "x" * 4097])
def test_invalid_bearer_token(token):
    with pytest.raises(ValidationError, match="Invalid MCP bearer credential"):
        McpStreamableHttpTransport(
            kind="streamable_http", url="https://remote.example/mcp", bearer_token=token
        )


def test_credential_redacted_and_preserved_by_python_snapshot():
    config = McpStreamableHttpTransport(
        kind="streamable_http", url="https://remote.example/mcp", bearer_token="private-test-secret"
    )
    assert "private-test-secret" not in repr(config)
    assert "private-test-secret" not in config.model_dump_json()
    assert McpStreamableHttpTransport.model_validate(config.model_dump()) == config


@pytest.fixture
def http_server(monkeypatch):
    source = Path(__file__).resolve().parents[2] / "examples" / "webhook_mcp_server" / "src"
    monkeypatch.syspath_prepend(str(source))
    from webhook_mcp_server.delivery_service import DeliveryService
    from webhook_mcp_server.server import build_server

    manager = StreamableHTTPSessionManager(
        build_server(DeliveryService("tenant_a")), json_response=True
    )
    calls = []

    @asynccontextmanager
    async def lifecycle():
        async with manager.run():
            yield

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            async with lifecycle():
                assert (await receive())["type"] == "lifespan.startup"
                await send({"type": "lifespan.startup.complete"})
                assert (await receive())["type"] == "lifespan.shutdown"
            await send({"type": "lifespan.shutdown.complete"})
            return
        authenticated = dict(scope["headers"]).get(b"authorization") == b"Bearer tenant-a-test"
        calls.append((scope["method"], scope["path"], authenticated))
        if scope["path"] == "/redirect":
            await RedirectResponse("/mcp", status_code=307)(scope, receive, send)
        elif not authenticated:
            await Response(status_code=401)(scope, receive, send)
        else:
            await manager.handle_request(scope, receive, send)

    with socket(AF_INET, SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(
            uvicorn.Config(app, lifespan="on", log_level="critical", timeout_graceful_shutdown=2)
        )
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 5
            while not server.started and thread.is_alive() and monotonic() < deadline:
                sleep(0.01)
            assert server.started
            yield url, calls
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


def _reader(url, *, token="tenant-a-test"):
    config = reference_configuration()
    config["transport"] = {"kind": "streamable_http", "url": url, "bearer_token": token}
    servers = McpServerConfigurations.model_validate({"servers": [config]})
    return McpWebhookDeliveryStatusReader(servers, McpConnectionManager(SdkMcpClientFactory()))


def test_http_lookup_authentication_and_session_termination(http_server, monkeypatch):
    url, calls = http_server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    result = _reader(url + "/mcp").read(
        delivery_id="only_a", tenant_id="tenant_a", timeout_seconds=5
    )
    assert result.status == "succeeded"
    assert all(path == "/mcp" and authenticated for _, path, authenticated in calls)
    assert calls[-1] == ("DELETE", "/mcp", True)


@pytest.mark.parametrize(
    "path,token,accepted",
    [
        ("/mcp", "bad-token", False),
        ("/redirect", "tenant-a-test", True),
    ],
)
def test_http_failure_never_retries_or_follows_redirect(http_server, path, token, accepted):
    url, calls = http_server
    with pytest.raises(WebhookServiceUnavailable, match="connection failed"):
        _reader(url + path, token=token).read(
            delivery_id="only_a", tenant_id="tenant_a", timeout_seconds=3
        )
    assert calls == [("POST", path, accepted)]
