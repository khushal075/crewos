"""Conservative schema pin comparison; no remote reference resolution or execution."""

import json

from crewos.mcp_integration.approved_tool_catalog import ApprovedMcpCatalogEntry
from crewos.mcp_integration.contracts import McpToolDescription


class McpSchemaMismatch(ValueError):
    """Advertised contract differs from a trusted expectation; omit remote details."""


def verify_schema_compatibility(
    entry: ApprovedMcpCatalogEntry, *, expected: McpToolDescription
) -> None:
    """Require exact JSON schema pins supplied independently by trusted application code.

    This checks identity and both input/output schemas, excluding descriptive text.
    Object-key order is ignored; array order and numeric representations remain
    significant. It intentionally rejects even potentially compatible changes.
    Never construct expected from the same live discovery being checked.

    This does not validate JSON Schema semantics, resolve references, validate
    invocation data, grant permissions or register an executable tool. A future
    registration path must call this check and still use local input/output models
    to validate actual data. Passing here does not authorize external references.
    """
    try:
        trusted = McpToolDescription.model_validate(expected.model_dump(mode="python"))
        advertised = entry.description
    except ValueError:
        raise McpSchemaMismatch("Invalid MCP schema metadata") from None
    if trusted.name != entry.remote_name or advertised.name != entry.remote_name:
        raise McpSchemaMismatch("MCP tool identity differs from trusted schema expectation")
    for actual, pinned in (
        (advertised.input_schema, trusted.input_schema),
        (advertised.output_schema, trusted.output_schema),
    ):
        if _schema_json(actual) != _schema_json(pinned):
            raise McpSchemaMismatch("MCP tool schema differs from trusted expectation")


def _schema_json(schema: object) -> str:
    # Comparing Python dictionaries would incorrectly consider True equal to 1.
    return json.dumps(
        schema, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
