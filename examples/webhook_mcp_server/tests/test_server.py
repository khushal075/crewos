"""Standalone example behavior; real protocol tests live in CrewOS's MCP suite."""

import pytest

from webhook_mcp_server.config import configured_tenant
from webhook_mcp_server.delivery_service import DeliveryService


def test_fixture_only_exposes_tenant_a_delivery():
    assert DeliveryService("tenant_a").read("only_a")["status"] == "succeeded"
    assert DeliveryService("tenant_b").read("only_a") == {
        "outcome": "unavailable",
        "delivery_id": "only_a",
    }


@pytest.mark.parametrize("tenant", ["", "bad tenant", "../tenant_a"])
def test_launcher_requires_valid_tenant(monkeypatch, tenant):
    monkeypatch.setenv("WEBHOOK_MCP_TENANT", tenant)
    with pytest.raises(ValueError, match="WEBHOOK_MCP_TENANT"):
        configured_tenant()
