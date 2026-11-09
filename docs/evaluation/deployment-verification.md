# Local deployment and observability verification

From the repository root, with Docker Compose available:

```sh
poetry run python -m crewos.evaluation.verify_deployment
```

The verifier builds the current image and starts a uniquely named Compose project
with isolated volumes, Redis, the API, and a Celery worker. It injects synthetic
credentials and a deterministic OpenAI-compatible fixture. No existing tenant
configuration, credentials, local volumes or published ports are used. It
cleans up its own containers and volumes. The
[recorded Compose run](reports/2026-11-09-agent-deployment.json) passed all seven checks.

The probe submits an authenticated API request and waits for the actual queued
job, verifies fallback after a synthetic primary 503, checks that an incident
note cannot be written before human review or written twice, and scores a
candidate against stored feedback. It also checks that the same run ID appears
in API and worker logs, and that worker operation counters are visible through
the authenticated scrape endpoint.

Set `AGENT_METRICS_TOKEN` to enable `GET /metrics` with a bearer credential.
Compose mounts the same metrics directory into API and workers and sets
`PROMETHEUS_MULTIPROC_DIR` before process startup. Use a fresh metrics directory
per deployment to avoid stale counts from previous processes; do not clear an
active directory. Scrape behind a private monitoring boundary. The endpoint
exposes metrics, and the logs correlate events by run ID. This run does not
verify a hosted cluster, live LLM quality, production scale or distributed tracing.

## Exact vector search decision

```sh
poetry run python -m crewos.evaluation.benchmark_exact_vector --candidates 512 --dimensions 768
```

This compares exact cosine scoring that normalizes the same query for each
candidate with the equivalent version that prepares the query once. It checks
that the two paths return identical top scores. This isolates in-memory scoring
and ranking from embedding generation, database reads, and network time.

At 512 candidates and 768 dimensions, the [recorded benchmark](reports/2026-11-09-exact-vector-benchmark.json)
measured 96.76 ms p95 before and 57.03 ms p95 with query preparation.
The top scores matched. Keep exact search within the current bounded corpus. Before adopting an
approximate index, profile representative tenant workloads end to end and
confirm scoring or candidate scale is the constraint. Then measure index
recall against exact results and verify tenant scoping, version visibility and
deletion handling. The synthetic benchmark is not a production latency SLO.
