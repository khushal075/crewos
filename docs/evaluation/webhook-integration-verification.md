# Read-only webhook integration verification

## Reproduce

Deterministic scripted agent orchestration with real loopback HTTP:

```bash
poetry run python -m crewos.evaluation.webhook_integration_demo
```

A real, already-installed local model choosing the registered tool:

```bash
poetry run python -m crewos.evaluation.webhook_integration_demo \
  --model qwen2.5-coder:14b-instruct-q4_K_M
```

The optional model uses `http://localhost:11434/v1` unless `--base-url` is supplied.
No model is downloaded. The demo starts a disposable reference service implementing
[the adapter contract](../architecture/webhook-integration.md#http-contract-and-tenant-trust-boundary).
It passes that service's HTTP reader to the actual registry/dispatcher and runs the
existing agent loop. The prompt explicitly requests this named tool and delivery;
this is a targeted wiring demonstration, not general autonomous planning evaluation.

The caller contexts and service tokens are synthetic. The reference server is a
lookup fixture with controlled error routes, not a webhook sending system or a
third-party provider. The only requests to it are GETs. No existing source database,
Redis queue, tenant grants or private `.env.local` file is changed. Other registered
tools are not permitted by the synthetic context. Operational logs go to stderr;
the JSON report goes to stdout. Failed checks return exit code 1; execution errors
propagate instead of generating successful evidence.

## Recorded local-model run: 6 November 2026

[Raw report](reports/2026-11-06-webhook-integration.json)

- Model: `qwen2.5-coder:14b-instruct-q4_K_M`.
- All **10 checks passed**.
- Four HTTP requests: one agent-selected delivery read, two tenant-B unavailable
  lookups and one deliberate HTTP 503 failure. Permission denial made no request.
- Whole run: **17.349488 seconds**, including model decisions and the HTTP checks.
- The model returned a succeeded delivery observation with its provider timestamp,
  while stating that this does not prove downstream business processing.

Checks cover registered tool selection, observation reaching the next decision,
requested delivery identity, denial before network I/O, indistinguishable missing/
other-tenant results, tenant-specific credentials, separate upstream failure,
read-only HTTP, correlation metadata and exclusion of credentials/response bodies
from captured logs.

This one local-model sample does not prove general answer truthfulness, independent
planning capability, hostile-provider resistance or performance under load. The
chat-model tag is recorded but its package digest is not pinned/verified by this
runner. No token billing or monetary cost measurement is claimed. It does not
exercise incoming API authentication or connect a named external vendor.

## Deterministic regression coverage

`tests/test_webhook_integration.py` additionally verifies fixture isolation,
invalid input/timeout admission, HTTPS/origin rules, credential representation,
real per-tenant authorization headers, redirects without forwarding, service errors
without body leakage, malformed/duplicate/identity-mismatched responses, byte and
representation limits, actual socket timeout, lazy configured registration,
configured positive dispatch, tool argument injection rejection, remaining timeout
propagation and a scripted agent loop reading real HTTP observations.

The existing contract tests retain field/outcome/status shape checks. Permission
policy and executor tests cover generic fail-closed enforcement. Safe tool logs
inherit run/call correlation when the lookup occurs inside an agent loop. The
verification is separate from enabling this tool in a real tenant deployment.
