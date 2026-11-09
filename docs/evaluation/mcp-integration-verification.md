# MCP verification walkthrough

From the repository root, run:

```sh
poetry install
poetry run python -m crewos.evaluation.mcp_integration_demo
poetry run pytest tests/mcp_integration -q
```

Read the trace in this order:

1. **Decision.** The scripted provider selects `get_webhook_delivery_status` with
   `delivery_id: only_a`. It plays the model's role deterministically.
2. **Permission.** The dispatcher validates the input and the existing policy
   gate checks `webhook_delivery.read` on CrewOS's trusted context.
3. **Connection.** The reader selects the configured server for the tenant and
   the SDK launches it as a child process. They exchange MCP messages on pipes.
4. **Discovery and call.** CrewOS matches the advertised name to the allowlist,
   checks both schemas against local expectations, then sends the delivery ID.
5. **Observation.** CrewOS checks the server's reported tenant and delivery
   identity, validates the structured result, closes the process and adds the
   result to the agent's next decision. The demo reports `succeeded`.

The report checks five cases: the observed result reaches the agent, a denied
request opens no session, another tenant cannot see the fixture and gets the
same outcome as a missing record, a misconfigured process tenant is rejected,
and a server that exits during startup produces an integration failure. SDK
tests also verify that child processes exit after cancellation and partial
initialization timeout. Contract tests cover schema drift, malformed results,
pagination bounds and explicit remote tool errors.

This proves local stdio interoperability with scripted agent decisions and
synthetic contexts. It does not demonstrate a live model's tool choice, network
MCP authorization, a vendor integration or production isolation. The demo
does not modify existing tenant data or configuration.

The [recorded demo report](reports/2026-11-09-mcp-webhook.json) passed all five
checks. The reference Docker image was also built locally and tested through
the same MCP reader using attached stdio with container networking disabled.
The reader returned the expected observation and the container exited.
