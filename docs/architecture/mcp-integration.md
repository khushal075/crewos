# MCP integration layout

Status: metadata, invocation and result contracts implemented. `contracts.py` now
defines normalized discovery/call/result models, and `tests/mcp_integration/test_contracts.py` contains
real shape/admission tests. Other proposed implementation/test files remain
placeholders. No MCP SDK, connection, executable server, tool registration or
configuration change has been added. We will discuss each remaining component
before implementing it.

```text
crewos/
├── mcp_integration/
│   ├── __init__.py
│   ├── contracts.py
│   ├── server_configuration.py
│   ├── mcp_client.py
│   ├── approved_tool_catalog.py
│   ├── connection_manager.py
│   └── adapters/
│       ├── __init__.py
│       ├── sdk_mcp_client.py
│       └── webhook_delivery_reader.py
└── core/
    └── mcp_setup.py

examples/webhook_mcp_server/
├── pyproject.toml                   # Comment-only packaging placeholder
├── Dockerfile                       # Comment-only image placeholder
├── .env.example                     # Comment-only configuration placeholder
├── README.md
├── src/webhook_mcp_server/
│   ├── __init__.py
│   ├── config.py
│   ├── server.py
│   └── delivery_service.py
└── tests/
    └── test_server.py

tests/mcp_integration/
├── __init__.py
├── test_contracts.py
├── test_server_configuration.py
├── test_approved_tool_catalog.py
├── test_connection_manager.py
├── test_sdk_mcp_client.py
├── test_webhook_delivery_reader.py
└── test_end_to_end.py

docs/
├── architecture/mcp-integration.md
├── adr/0002-mcp-client-module-boundary.md
└── evaluation/
    ├── mcp-integration-verification.md
    └── reports/                     # Existing directory; no MCP report yet
```

Existing delivery contracts, handler, HTTP adapter and tool composition remain in
their current locations. They have not been changed by this scaffold.


## First contract: advertised tool metadata

`McpToolDescription` is exported from `crewos.mcp_integration`. It describes remote
metadata, not an executable CrewOS `ToolDefinition`:

| Field | Admission and meaning |
| --- | --- |
| `name` | Required; 1–128 ASCII letters/digits, underscores, dots or hyphens. No trimming or normalization. |
| `description` | Optional server-provided text; when present, nonblank and at most 4096 characters. Untrusted instructions remain data. |
| `input_schema` | Required JSON object explicitly declaring `type: object`. |
| `output_schema` | Optional JSON object with the same root requirement. |

These are CrewOS admission rules for the initial normalized representation, not a
claim to implement every MCP wire field or schema form. An SDK adapter will later
translate wire names such as `inputSchema` into these internal fields; it does not
exist yet. The existing delivery input can supply its advertised argument shape
through `GetWebhookDeliveryStatusInput.model_json_schema()`.

The model rejects extra authority/handler fields, non-JSON values, non-finite numbers
and total metadata above 65,536 UTF-8 bytes. It preserves admitted schema content.
It does **not** check all JSON Schema keyword semantics, validate future call
arguments, resolve `$ref` targets, prove a server's claims, establish tenant identity,
or approve/register a tool. A root-shaped schema can still be semantically invalid;
full schema compatibility/argument validation remains a later execution boundary.
Transport-level limits must also apply before loading remote messages.

Field reassignment is frozen. Nested schema dictionaries are not deeply immutable;
consumers must snapshot/revalidate them at later trust boundaries. Input dictionaries
are copied through validation so mutating the caller's original metadata does not
change the newly created model. Descriptions are not executed or added to model
prompts by this increment. No permission assignment or replay-safety assertion is
accepted at the top level of this contract.

Tests cover schema generation/round-trip, names, descriptions, root requirements,
strict JSON, UTF-8 limits, forbidden authority claims and mutation boundaries. This
is contract evidence only, not MCP protocol interoperability verification. Tool-call
and result contracts are now defined below; trusted configuration, client interface
and transport remain subsequent increments.


## Tool-call and result contracts

`McpToolCallRequest` carries `tool_name` (the same admitted name format as discovery)
and `arguments` (a JSON object, defaulting to empty). Total serialized size is at
most 32,768 UTF-8 bytes; non-finite numbers/non-JSON objects are rejected. It does
not contain server URLs, credentials, tenant context, approval or timeout fields.
A remote argument named `tenant_id` is still arbitrary argument data, not the
caller's verified identity. The approved domain adapter must control its mapping.
Generic argument admission does not validate arguments against the advertised schema.

`McpToolCallResult` contains:

| Field | Meaning |
| --- | --- |
| `is_error` | Required strict boolean; whether the remote tool reported execution failure. |
| `content` | Up to 16 `McpTextContent` blocks, each containing exact text of at most 16,384 characters. Empty text/list is valid. |
| `structured_content` | Optional strict JSON object; remains untrusted domain data. |

The whole result is bounded to 65,536 serialized UTF-8 bytes, including text and
structured data. Values are never truncated. Images, audio, embedded resources
and other MCP content kinds are unsupported by this initial normalized contract;
future SDK translation must reject or explicitly support them, never silently
omit unsupported blocks. Both text and structured content may be present; this
shape does not establish that they agree or satisfy an advertised output schema.

The future SDK adapter must populate `is_error` explicitly, including applying the
selected protocol's semantics when its wire field is omitted. An error result is
not a missing domain record, even if structured data claims `outcome=unavailable`.
A domain adapter must check the error flag before interpreting a success payload.
Transport/protocol failures must propagate separately; this contract must not be
used to invent empty success after a failed request. Conversely, an empty success
result alone does not establish that a delivery is missing.

Connection/server identity, tenant scope, request correlation, authorization and
remaining timeout belong to trusted execution/session layers outside these payloads.
The result cannot prove which server/call/tenant produced it. No tool is invoked by
constructing these objects. Metadata/content can contain instruction-like strings;
validation does not make them trusted instructions or prevent prompt injection.

All models forbid extra top-level fields and prevent field reassignment. Nested
argument/structured dictionaries remain mutable. Future consumers should validate
from a Python-mode dump before serialization when handling existing model instances;
JSON serialization alone can normalize non-finite values after a bypassed mutation.
Call input dictionaries are copied during validation. Actual transport must enforce
wire limits before parsing; these are post-parsing admission limits.

Tests cover round-trip, strict booleans/JSON, field/byte/count limits, forbidden
connection/authority fields, content-kind rejection, exact text preservation, error
versus domain outcome and mutation/revalidation. SDK/connection/server scaffolds
remain unchanged and no protocol interoperability is claimed.
