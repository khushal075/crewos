# MCP integration layout

Status: discovery/call/result contracts and trusted stdio server configuration
are implemented, with focused tests. The client interface is defined. Remaining
catalog, connection, SDK, server and domain-adapter files are still placeholders. No MCP SDK,
connection, executable server, tool registration or runtime configuration wiring
has been added. We will discuss each remaining component
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


## Trusted local-server configuration

`server_configuration.py` defines four data-only models:

- `McpStdioTransport`: absolute executable path, literal argument tuple, optional
  absolute working directory and explicitly supplied environment values.
- `ApprovedMcpTool`: remote tool name, local alias, required CrewOS permission and
  operator-assigned replay safety (false by default).
- `McpServerConfiguration`: server ID, tenant ID, transport, enable flag, timeout
  and tool allowlist. Defaults are disabled, a five-second timeout and no tools.
- `McpServerConfigurations`: bounded set of server definitions; empty by default.

`stdio` means the future client starts a local child process and exchanges protocol
messages through stdin/stdout. Only this transport is admitted initially. HTTP MCP
configuration/authorization is not implemented by these models. Absolute paths are
validated syntactically using the host platform; existence/permissions are not
checked and no process is started. Arguments remain individual strings, not a
shell command. The configured executable may itself run arbitrary code, including a
shell: operator approval is essential and this is not a process sandbox.

A server definition belongs to one tenant. Server IDs and local aliases may repeat
across tenants; duplicates within a tenant are rejected. A server has at most 16
allowlisted tools, with unique remote names and local aliases. A configuration set
has at most 32 servers. Aliases are also unique across configured servers in one
tenant, including disabled definitions to reserve their names. Collisions against
existing non-MCP registry tools must be checked later during composition.

Permissions are explicit action names such as `webhook_delivery.read`, not wildcards
or automatically inferred roles. A model/server cannot grant these by supplying
metadata. Constructing `ApprovedMcpTool` does not prove trusted provenance or grant
the caller a permission; only trusted operator configuration may supply it. The
future handler must still enforce permissions on each invocation. Remote read-only
annotations do not establish replay safety. Tool-schema compatibility and changes
must be checked separately by the future approved-tool/catalog/client layers.

Configuration admits at most 64 arguments of 2048 characters, 32 explicit environment
entries with portable names, and environment values up to 4096 characters. NUL is
rejected. Environment values use `SecretStr`; repr/JSON serialization redact them.
Use environment/secret management rather than command arguments for credentials.
Redacted JSON is not a secret backup: trusted Python-mode revalidation preserves
SecretStr values. These models do not inherit a parent process environment; the
future SDK adapter must deliberately choose and enforce its environment policy.
Nested dictionaries can still be mutated, so later boundaries must snapshot and
revalidate configuration rather than assume deep immutability.

The timeout is a finite positive value at most 30 seconds. It is stored configuration
only; a future transport must enforce it and cap it by remaining execution budget.
Nothing reads a private environment file, launches a process, discovers tools,
registers handlers or changes application settings through constructing this model.
The configuration is not wired into CrewOS settings yet. The client interface is
now defined; the approved-tool catalog and connection/SDK implementation remain next.


## MCP client interface

`mcp_client.py` defines `McpClientInterface`, a structural `Protocol` for one
tenant/server session. Its asynchronous methods are `discover_tools`, `call_tool`
and `close`. The future connection manager creates the initialized session from
trusted configuration. No model-supplied tenant, executable or credential selects
its connection. No SDK, subprocess or application wiring is implemented yet.

Discovery returns normalized descriptions, not authorized tools. The interface
requires complete pagination within one time allowance, at most 128 tools and
1 MiB of combined compact UTF-8 normalized metadata JSON. Duplicate names,
oversized data or unsupported protocol content must fail rather than disappear
through truncation. These are CrewOS admission requirements, not MCP standard
limits; adapter tests must verify their enforcement later.

Calls require prior CrewOS authorization and approved-schema validation. A valid
remote tool error returns `is_error=True`; `McpConnectionError` represents connection
or timeout failures, while `McpProtocolError` represents malformed or unsupported
data. Neither means the business record is missing. Calls must not retry
automatically because a missing response does not prove an action failed to run.
Timeouts must be finite, positive and capped by configuration and remaining caller
budget across all waits. Cleanup must be bounded, cancellation must propagate, and
repeated close must be safe. These are implementation obligations, not behavior
that a Python Protocol enforces. The next increment is the approved-tool catalog.
