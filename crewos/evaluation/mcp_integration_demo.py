"""Repeatable agent-to-MCP demo using a real subprocess and scripted decisions."""

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.model_decision import FinalAnswerDecision, ToolCallDecision
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.interfaces.webhook_delivery_status_reader import (
    WebhookResponseInvalid,
    WebhookServiceUnavailable,
)
from crewos.application.services.policy_executor import PolicyDeniedError
from crewos.application.use_cases.agent_loop import AgentLoop
from crewos.mcp_integration.adapters.sdk_mcp_client import SdkMcpClientFactory
from crewos.mcp_integration.adapters.webhook_delivery_reader import (
    TOOL_NAME,
    McpWebhookDeliveryStatusReader,
)
from crewos.mcp_integration.connection_manager import McpConnectionManager
from crewos.mcp_integration.server_configuration import McpServerConfigurations

_EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "webhook_mcp_server" / "src"


def reference_configuration(*, tenant="tenant_a", launched_tenant=None, broken=False):
    """Build a trusted launcher for the checkout demo; no shell or private settings."""
    program = (
        "raise SystemExit(7)"
        if broken
        else f"import sys; sys.path.insert(0, {str(_EXAMPLE)!r}); "
        "from webhook_mcp_server.server import main; main()"
    )
    return {
        "server_id": "webhooks",
        "tenant_id": tenant,
        "enabled": True,
        "timeout_seconds": 5,
        "transport": {
            "command": str(Path(sys.executable).absolute()),
            "args": ["-I", "-c", program],
            "environment": {"WEBHOOK_MCP_TENANT": launched_tenant or tenant},
        },
        "allowed_tools": [
            {
                "remote_name": TOOL_NAME,
                "local_name": TOOL_NAME,
                "required_permission": "webhook_delivery.read",
            }
        ],
    }


class CountingFactory(SdkMcpClientFactory):
    def __init__(self):
        self.opens = 0

    async def connect(self, configuration, *, timeout_seconds):
        self.opens += 1
        return await super().connect(configuration, timeout_seconds=timeout_seconds)


class _UnusedTaskReader:
    def read(self, **kwargs):
        raise AssertionError("Unexpected task status request")


class _Decisions:
    def __init__(self):
        self.observation = None

    def decide(self, *, messages, tools, timeout_seconds):
        for message in reversed(messages):
            if message.role == "tool":
                self.observation = json.loads(message.content)["result"]
                return FinalAnswerDecision(
                    type="final_answer",
                    answer=f"Observed webhook status: {self.observation['status']}.",
                )
        assert any(tool.name == TOOL_NAME for tool in tools)
        return ToolCallDecision(
            type="tool_call", name=TOOL_NAME, arguments={"delivery_id": "only_a"}
        )


def run_mcp_demo() -> dict:
    from crewos.core.tool_setup import build_tool_dispatcher

    factory = CountingFactory()
    servers = McpServerConfigurations.model_validate(
        {
            "servers": [
                reference_configuration(),
                reference_configuration(tenant="tenant_b"),
                reference_configuration(tenant="wrong_scope", launched_tenant="tenant_a"),
                reference_configuration(tenant="broken", broken=True),
            ]
        }
    )
    reader = McpWebhookDeliveryStatusReader(servers, McpConnectionManager(factory))
    dispatcher = build_tool_dispatcher(reader=_UnusedTaskReader(), webhook_reader=reader)

    def context(tenant, authorized=True):
        return ToolExecutionContext(
            tenant_id=tenant, permissions=frozenset({"webhook_delivery.read"} if authorized else ())
        )

    def lookup(tenant, delivery="only_a", authorized=True):
        return dispatcher.dispatch(
            name=TOOL_NAME, arguments={"delivery_id": delivery}, context=context(tenant, authorized)
        )

    decisions = _Decisions()
    result = AgentLoop(provider=decisions, dispatcher=dispatcher, max_decisions=3).run(
        messages=[DecisionMessage(role="user", content="Read the status of webhook only_a.")],
        context=context("tenant_a"),
    )
    checks = {
        "real_mcp_observation_reached_agent": (
            factory.opens == 1
            and decisions.observation is not None
            and decisions.observation["status"] == "succeeded"
        )
    }
    before = factory.opens
    try:
        lookup("tenant_a", authorized=False)
    except PolicyDeniedError:
        checks["permission_denied_before_process_start"] = factory.opens == before
    else:
        checks["permission_denied_before_process_start"] = False
    cross = lookup("tenant_b")
    missing = lookup("tenant_b", "missing")
    checks["other_tenant_matches_missing"] = cross.outcome == missing.outcome == "unavailable"
    for tenant, error, check in (
        ("wrong_scope", WebhookResponseInvalid, "mismatched_server_tenant_rejected"),
        ("broken", WebhookServiceUnavailable, "server_failure_not_a_missing_record"),
    ):
        try:
            lookup(tenant)
        except error:
            checks[check] = True
        else:
            checks[check] = False
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "sessions_started": factory.opens,
        "decision_source": "scripted",
        "final_answer": result.answer,
        "scope": "Real stdio MCP, synthetic tenant data and contexts; no live LLM or vendor service.",
    }


if __name__ == "__main__":
    with redirect_stdout(sys.stderr):
        report = run_mcp_demo()
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
