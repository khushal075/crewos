# MCP integration layout

Status: scaffold only. The files below reserve the proposed locations. No MCP
SDK, connection, executable server, tool registration or configuration change has
been added. Placeholder test files contain no tests and prove no functionality.
We will discuss responsibilities and implementation before filling these files.

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
