"""Admission tests: discovery never expands trusted mappings."""

import pytest

from crewos.mcp_integration.approved_tool_catalog import ApprovedMcpToolCatalog, McpCatalogError
from crewos.mcp_integration.contracts import McpToolDescription
from crewos.mcp_integration.server_configuration import McpServerConfiguration


def config(**overrides):
    values = dict(
        tenant_id="tenant_a",
        server_id="webhooks",
        enabled=True,
        transport={"command": "/usr/bin/python3"},
        allowed_tools=[
            dict(
                remote_name="status",
                local_name="delivery_status",
                required_permission="webhook_delivery.read",
            )
        ],
    )
    values.update(overrides)
    return McpServerConfiguration.model_validate(values)


def tool(name="status", **overrides):
    return McpToolDescription(name=name, input_schema={"type": "object"}, **overrides)


def test_mapping_preserves_operator_authority_and_filters_extra_tools():
    catalog = ApprovedMcpToolCatalog(config(), (tool(), tool("retry")))
    assert len(catalog.entries) == 1
    entry = catalog.resolve("delivery_status")
    assert (entry.tenant_id, entry.server_id, entry.remote_name) == (
        "tenant_a",
        "webhooks",
        "status",
    )
    assert entry.required_permission == "webhook_delivery.read"
    assert entry.replay_safe is False
    with pytest.raises(McpCatalogError):
        catalog.resolve("retry")


@pytest.mark.parametrize("overrides", [{"enabled": False}, {"allowed_tools": []}])
def test_defaults_expose_no_tools(overrides):
    assert ApprovedMcpToolCatalog(config(**overrides), (tool(),)).entries == ()


def test_missing_configured_tool_fails_closed():
    with pytest.raises(McpCatalogError, match="missing"):
        ApprovedMcpToolCatalog(config(), (tool("other"),))


def test_duplicate_unapproved_names_also_rejected():
    with pytest.raises(McpCatalogError, match="Duplicate"):
        ApprovedMcpToolCatalog(config(), (tool(), tool("other"), tool("other")))


def test_metadata_mutation_cannot_change_snapshot():
    original = tool()
    catalog = ApprovedMcpToolCatalog(config(), (original,))
    original.input_schema["type"] = "array"
    entry = catalog.resolve("delivery_status")
    entry.description.input_schema["type"] = "array"
    assert entry.description.input_schema == {"type": "object"}


def test_bypassed_metadata_validation_is_rechecked():
    original = tool()
    original.input_schema["value"] = float("nan")
    with pytest.raises(McpCatalogError, match="Invalid"):
        ApprovedMcpToolCatalog(config(), (original,))


def test_tool_count_bound():
    with pytest.raises(McpCatalogError, match="count"):
        ApprovedMcpToolCatalog(config(), tuple(tool(f"t{i}") for i in range(129)))


def test_aggregate_metadata_bound():
    tools = tuple(
        tool(f"t{i}", output_schema={"type": "object", "padding": "x" * 60000}) for i in range(18)
    )
    with pytest.raises(McpCatalogError, match="size"):
        ApprovedMcpToolCatalog(config(), tools)


def test_tenant_catalogs_remain_separate():
    a = ApprovedMcpToolCatalog(config(), (tool(),))
    b = ApprovedMcpToolCatalog(config(tenant_id="tenant_b"), (tool(),))
    assert a.resolve("delivery_status").tenant_id != b.resolve("delivery_status").tenant_id
