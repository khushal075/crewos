# Reference webhook MCP server

This standalone, read-only example exposes `get_webhook_delivery_status` over
MCP stdio. The launcher assigns one synthetic tenant using
`WEBHOOK_MCP_TENANT`. The request contains only a `delivery_id`. For
`tenant_a`, `only_a` is reported as succeeded; other lookups are unavailable.

## Run with CrewOS from the repository root

```sh
poetry install
poetry run python -m crewos.evaluation.mcp_integration_demo
```

The demo launches this server automatically using CrewOS's Python interpreter.
Its JSON report goes to stdout; CrewOS logs go to stderr. No model or Redis is
required. A bare server process waits for protocol messages on stdin.

## Install or package the server independently

```sh
python3 -m venv /tmp/crewos-webhook-server
/tmp/crewos-webhook-server/bin/pip install ./examples/webhook_mcp_server
WEBHOOK_MCP_TENANT=tenant_a /tmp/crewos-webhook-server/bin/webhook-mcp-server
```

Or build an image from the example's own build context:

```sh
docker build -t crewos-webhook-mcp-reference:local examples/webhook_mcp_server
docker run --rm -i --network none -e WEBHOOK_MCP_TENANT=tenant_a crewos-webhook-mcp-reference:local
```

Do not allocate a TTY for stdio. A client can launch the same Docker command and
use the container's attached stdin/stdout. There is no listening port. Allowing
CrewOS to invoke Docker requires access to the Docker daemon; use the local
executable for the normal demo. The fixture is for interoperability and learning.
It does not authenticate a network client or contact a real webhook vendor.
