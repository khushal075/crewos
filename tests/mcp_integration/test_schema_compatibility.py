"""Trusted schema expectations must remain independent of advertised metadata."""

import pytest

from crewos.mcp_integration.approved_tool_catalog import ApprovedMcpToolCatalog
from crewos.mcp_integration.contracts import McpToolDescription
from crewos.mcp_integration.schema_compatibility import (
    McpSchemaMismatch,
    verify_schema_compatibility,
)
from crewos.mcp_integration.server_configuration import McpServerConfiguration


def entry(input_schema=None, output_schema=None):
    config = McpServerConfiguration.model_validate(
        dict(
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
    )
    description = McpToolDescription(
        name="status",
        input_schema=input_schema or {"type": "object"},
        output_schema=output_schema,
    )
    return ApprovedMcpToolCatalog(config, (description,)).resolve("delivery_status")


def test_key_order_and_description_do_not_change_schema_match():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    expected = McpToolDescription(
        name="status",
        description="Trusted description",
        input_schema=dict(reversed(list(schema.items()))),
    )
    verify_schema_compatibility(entry(schema), expected=expected)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "required": ["id"]},
        {"type": "object", "additionalProperties": False},
        {"type": "object", "properties": {"id": {"type": "integer"}}},
    ],
)
def test_input_drift_is_rejected(schema):
    with pytest.raises(McpSchemaMismatch, match="schema differs"):
        verify_schema_compatibility(
            entry(schema),
            expected=McpToolDescription(name="status", input_schema={"type": "object"}),
        )


@pytest.mark.parametrize(
    "actual,pinned",
    [
        (None, {"type": "object"}),
        ({"type": "object"}, None),
        ({"type": "object", "required": ["status"]}, {"type": "object"}),
    ],
)
def test_output_drift_including_missing_schema_is_rejected(actual, pinned):
    with pytest.raises(McpSchemaMismatch):
        verify_schema_compatibility(
            entry(output_schema=actual),
            expected=McpToolDescription(
                name="status", input_schema={"type": "object"}, output_schema=pinned
            ),
        )


def test_both_output_schemas_can_match():
    verify_schema_compatibility(
        entry(output_schema={"type": "object"}),
        expected=McpToolDescription(
            name="status", input_schema={"type": "object"}, output_schema={"type": "object"}
        ),
    )


def test_wrong_remote_identity_rejected():
    with pytest.raises(McpSchemaMismatch, match="identity"):
        verify_schema_compatibility(
            entry(), expected=McpToolDescription(name="other", input_schema={"type": "object"})
        )


def test_json_boolean_is_not_numeric_one():
    with pytest.raises(McpSchemaMismatch):
        verify_schema_compatibility(
            entry({"type": "object", "default": True}),
            expected=McpToolDescription(
                name="status", input_schema={"type": "object", "default": 1}
            ),
        )


def test_mutated_trusted_expectation_revalidated_without_leaking_payload():
    expected = McpToolDescription(name="status", input_schema={"type": "object"})
    expected.input_schema["secret"] = float("nan")
    with pytest.raises(McpSchemaMismatch, match="^Invalid MCP schema metadata$"):
        verify_schema_compatibility(entry(), expected=expected)
