"""Real MCP subprocess integration through CrewOS policy and agent loop."""

import pytest

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.webhook_delivery_status_reader import WebhookServiceUnavailable
from crewos.core.config import settings
from crewos.core.tool_setup import build_tool_dispatcher
from crewos.evaluation.mcp_integration_demo import reference_configuration, run_mcp_demo
from crewos.mcp_integration.adapters.webhook_delivery_reader import TOOL_NAME
from crewos.mcp_integration.server_configuration import McpServerConfigurations


def test_real_mcp_agent_demo():
    report = run_mcp_demo()
    assert report["passed"], report
    assert report["sessions_started"] == 5


def test_settings_enable_real_mcp_reader_without_http_fallback(monkeypatch):
    monkeypatch.setattr(settings, "MCP_WEBHOOK_ENABLED", True)
    monkeypatch.setattr(settings, "WEBHOOK_INTEGRATION_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "MCP_WEBHOOK_SERVERS",
        McpServerConfigurations.model_validate({"servers": [reference_configuration()]}),
    )
    dispatcher = build_tool_dispatcher(reader=None)
    assert not dispatcher.replay_safe(TOOL_NAME)
    for tenant in ("tenant_a", "unconfigured"):
        context = ToolExecutionContext(
            tenant_id=tenant, permissions=frozenset({"webhook_delivery.read"})
        )
        if tenant == "tenant_a":
            result = dispatcher.dispatch(
                name=TOOL_NAME, arguments={"delivery_id": "only_a"}, context=context
            )
            assert result.status == "succeeded"
        else:
            with pytest.raises(WebhookServiceUnavailable, match="No approved MCP"):
                dispatcher.dispatch(
                    name=TOOL_NAME, arguments={"delivery_id": "only_a"}, context=context
                )
