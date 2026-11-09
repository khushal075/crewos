# ADR 0002: MCP client module boundary

Status: accepted for the read-only webhook integration.

CrewOS keeps MCP client code in `mcp_integration`. The webhook handler continues
to enforce `webhook_delivery.read` and consume the existing domain result. An
explicit composition setting selects either the HTTP reader or the MCP reader;
an MCP failure never falls back to HTTP.

Use a local stdio executable for the first transport. One connection is owned by
one execution. The SDK adapter owns its transport and session in the same task
and the connection manager bounds initialization and cleanup. A separately
packaged reference server demonstrates the protocol across a process boundary.

We considered automatic registration of discovered tools and shared session
pooling. Automatic registration would give advertised names too much authority.
Pooling would require tenant-scoped lifecycle, invalidation and reuse rules.
For this increment, only the known webhook tool is wired. CrewOS checks tenant
scope, the configured name/permission mapping and pinned schemas before calling.
MCP webhook calls are marked unsafe for automatic replay after an uncertain run.

The demo trusts the configured executable and uses fixtures. Streamable HTTP is
also available for trusted endpoints with pre-provisioned tenant bearer tokens.
OAuth flows, arbitrary tool registration, a shared pool and production process
isolation are separate design work. The example can be packaged independently. A later repo
refactor will preserve the in-process module option and define startup,
configuration and Docker packaging for each service that can deploy separately.
