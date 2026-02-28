# CrewOS — Async AI Agent Orchestration Platform

A production-grade platform for orchestrating multi-agent AI pipelines with async task processing, multi-tenancy, and flexible deployment. Built to demonstrate distributed systems design, async processing patterns, and LLM agent orchestration.

---

## What It Does

CrewOS lets you submit a prompt via API and get back a structured response produced by a **crew of two specialized AI agents** — a Research Specialist and a Processing Specialist — running sequentially on a local LLM.

The system is designed to handle slow LLM inference without blocking: tasks are processed asynchronously via Celery workers, and clients poll for results using a task ID.

Multi-tenant from the ground up — each request carries a tenant context via the `X-Tenant-ID` header, and in Kubernetes each tenant gets its own dedicated worker pod and task queue (`crewai.<tenant_id>`).

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        UI / Client                          │
│                                                             │
│   1. POST /domain/run  ───────────────────────────────┐    │
│   2. GET  /domain/status/{task_id}  (poll until done) ←┘   │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌────────────────────────────────┐
│         FastAPI Backend        │
│  - Extracts X-Tenant-ID header │
│  - Creates Celery task         │
│  - Returns task_id immediately │
└────────────────┬───────────────┘
                 │ delay(tenant_id, payload)
                 ▼
┌────────────────────────────────┐
│      Redis (Broker + Store)    │
│  - Task queue      (DB 0)      │
│  - Result backend  (DB 1)      │
└────────────────┬───────────────┘
                 │ consume
                 ▼
┌────────────────────────────────┐
│         Celery Worker          │
│                                │
│  ┌──────────────────────────┐  │
│  │   Research Specialist    │  │
│  │   "Analyze & extract"    │  │
│  └────────────┬─────────────┘  │
│               │ result stored  │
│               │ on task.result │
│  ┌────────────▼─────────────┐  │
│  │  Processing Specialist   │  │
│  │  receives context from   │  │
│  │  Research Specialist     │  │
│  └────────────┬─────────────┘  │
└───────────────┼────────────────┘
                │ final result stored in Redis
                ▼
       Client polls & retrieves
```

### Why Polling?

LLM inference can take 10–60 seconds. Holding an HTTP connection open that long is impractical at scale. Instead:

1. `POST /domain/run` returns a `task_id` immediately (non-blocking)
2. The client polls `GET /domain/status/{task_id}` until status is `SUCCESS`
3. The completed result is returned from Redis

This is the same pattern used by OpenAI's batch API and Stripe's async jobs.

---

## Agent Pipeline

Agents are created via a factory with an injected LLM — the application layer is fully decoupled from any specific LLM provider. Tasks are explicitly chained using a `context` field, so each agent builds on the previous agent's output:

```
User Prompt
     │
     ▼
┌─────────────────────────────────────┐
│  Research Specialist                │
│  Goal: Analyze problems deeply      │
│        and extract key insights     │
│  result stored on task.result       │
└─────────────────┬───────────────────┘
                  │ task.result passed as context
                  ▼
┌─────────────────────────────────────┐
│  Processing Specialist              │
│  Goal: Generate a direct, concise   │
│        response from research       │
│  context = [analysis_task]          │
└─────────────────┬───────────────────┘
                  │
                  ▼
            Final Response
```

Task chaining is explicit — `processing_task.context = [analysis_task]` — so Processing Specialist always receives Research Specialist's output before generating the final answer. The `Crew.kickoff()` respects task-agent assignment and context, running each task with its assigned agent only.

The LLM is injected into agents at runtime — swapping Ollama for OpenAI or any other provider requires no changes to agent or task logic.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI |
| Task Queue | Celery |
| Broker & Result Store | Redis |
| LLM Server | Ollama (llama3) |
| Agent Framework | Custom (CrewAI-inspired) |
| Config | Pydantic Settings v2 |
| Packaging | Poetry |
| Containers | Docker / Docker Compose |
| Orchestration | Kubernetes |

---

## Running the Project

Choose the deployment option that suits your needs. The same application code runs across all three — only infrastructure config changes.

---

### Option 1 — Local (Development)

Best for debugging and development. Requires Redis and Ollama installed locally.

**Prerequisites:** Python 3.11+, Poetry, Redis, Ollama

```bash
# 1. Clone the repo
git clone <repo-url>
cd crewai-platform

# 2. Install dependencies
poetry install

# 3. Start Redis
redis-server

# 4. Start Ollama and pull a model
ollama serve
ollama pull llama3:8b-instruct-q4_K_M

# 5. Create your environment file
touch k8crew.env
```

Add the following to `k8crew.env`:

```env
# Redis
REDIS_BROKER_URL=redis://localhost:6379/0
REDIS_RESULT_BACKEND=redis://localhost:6379/1

# Celery
CELERY_QUEUE_NAME=crewai
CELERY_CONCURRENCY=10

# LLM
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3:8b-instruct-q4_K_M
API_KEY=

# Tenant
TENANT_ID=1
DEFAULT_TENANT_ID=1

# Logging
LOGGING_LEVEL=INFO
```

```bash
# 6. Start the API (terminal 1)
poetry run dev

# 7. Start the Celery worker (terminal 2)
# TENANT_ID drives which queue the worker listens on: crewai.1
TENANT_ID=1 poetry run worker
```

API available at: `http://localhost:8000`

**To simulate two tenants locally**, run a second worker in a third terminal:

```bash
TENANT_ID=2 poetry run worker
```

Each worker only processes tasks from its own queue — tenant 1's tasks never touch tenant 2's worker.

---

### Option 2 — Docker Compose (Containerized)

Best for running the full stack with a single command. Each service runs in its own isolated container — no local Redis or Ollama required.

**Container Networking**

When running via Docker Compose, containers communicate over Docker's internal virtual network. Each container is reachable by its service name as defined in `docker-compose.yml`:

| | Local | Docker Compose |
|---|---|---|
| Redis | `redis://localhost:6379` | `redis://redis:6379` |
| Ollama | `http://localhost:11434` | `http://ollama:11434` |

```bash
# 1. Clone the repo
git clone <repo-url>
cd crewai-platform

# 2. Create your environment file
touch k8crew.env
```

Add the following to `k8crew.env`:

```env
# Redis (Docker service name)
REDIS_BROKER_URL=redis://redis:6379/0
REDIS_RESULT_BACKEND=redis://redis:6379/1

# Celery
CELERY_QUEUE_NAME=crewai
CELERY_CONCURRENCY=10

# LLM (Docker service name)
LLM_BASE_URL=http://ollama:11434/v1
LLM_MODEL=llama3:8b-instruct-q4_K_M
API_KEY=

# Tenant
TENANT_ID=1
DEFAULT_TENANT_ID=1

# Logging
LOGGING_LEVEL=INFO
```

```bash
# 3. Start all services
docker compose up --build

# 4. Pull the LLM model into the Ollama container (first run only)
docker exec crewai_ollama ollama pull llama3:8b-instruct-q4_K_M
```

API available at: `http://localhost:8000`

Services started:

| Container | Role | Port |
|---|---|---|
| `crewai_backend` | FastAPI API | 8000 |
| `crewai_worker` | Celery worker | — |
| `crewai_redis` | Redis broker + store | 6379 |
| `crewai_ollama` | Ollama LLM server | 11434 |

---

### Option 3 — Kubernetes (Production)

Best for scalable production deployments. API and workers scale independently. Each tenant gets a dedicated worker deployment with its own queue.

```bash
# 1. Create namespace
kubectl apply -f k8s/base/namespace.yaml

# 2. Apply config and secrets
kubectl apply -f k8s/base/configmap.yaml
kubectl apply -f k8s/base/secret.yaml

# 3. Deploy infrastructure
kubectl apply -f k8s/base/redis/
kubectl apply -f k8s/base/ollama/

# 4. Deploy application
kubectl apply -f k8s/base/api/
kubectl apply -f k8s/base/worker/

# 5. Apply ingress
kubectl apply -f k8s/base/ingress.yaml

# 6. Pull the LLM model (first run only)
kubectl exec -it deployment/crewai-ollama -- ollama pull llama3:8b-instruct-q4_K_M
```

Each tenant worker deployment sets `TENANT_ID` as an env var — the worker automatically listens on `crewai.<TENANT_ID>`:

```yaml
env:
  - name: TENANT_ID
    value: "tenant_a"
  - name: CELERY_QUEUE_NAME
    value: "crewai"
```

Scale workers independently without touching the API:

```bash
kubectl scale deployment crewos-worker-tenant-a --replicas=5
```

---

## API Reference

All endpoints require the `X-Tenant-ID` header.

### Submit a Task (Async)

```bash
curl -X POST http://localhost:8000/domain/run \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 1" \
  -d '{"agent_type": "research", "input": {"message": "What is engineering?"}}'
```

```json
{
  "task_id": "370d94b6-b5ad-4e6d-85ba-e497ff4b87cd",
  "status": "Queued"
}
```

### Poll for Result

```bash
curl http://localhost:8000/domain/status/370d94b6-b5ad-4e6d-85ba-e497ff4b87cd \
  -H "X-Tenant-ID: 1"
```

```json
{
  "task_id": "370d94b6-...",
  "status": "SUCCESS",
  "result": { ... }
}
```

Possible status values: `PENDING` → `STARTED` → `SUCCESS` / `FAILURE`

### Submit a Task (Sync — for testing)

Bypasses Celery entirely. Useful for local debugging without a running worker.

```bash
curl -X POST http://localhost:8000/domain/run-sync \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 1" \
  -d '{"agent_type": "research", "input": {"message": "What is engineering?"}}'
```

### Health Check

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

---

## Project Structure

```
crewos/
├── api/              # FastAPI routes and request/response schemas
│   └── middleware/   # Request ID and logging middleware
├── application/      # Use cases, DTOs — pure business logic
│   ├── dtos/         # Data transfer objects
│   ├── interfaces/   # LLM provider interface (decouples domain from infra)
│   └── use_cases/    # run_crew use case
├── core/             # App config, worker entrypoint, tenant context
├── domain/           # Entities, factories — zero framework dependencies
│   ├── entities/     # Agent, Task, Crew domain objects
│   └── factories/    # AgentFactory, TaskFactory, CrewFactory
├── infrastructure/   # Celery, Redis, structured logging
│   └── memory/       # Redis memory (planned)
├── services/         # CrewRunner — wires LLM provider + CrewFactory
├── third_party/      # LLM adapters (Ollama)
├── utils/            # Retry utilities (planned)
└── workers/          # Celery task definitions

k8s/
├── base/             # K8s manifests (api, worker, redis, ollama)
├── overlays/         # Environment patches (dev / staging / prod)
└── tenants/          # Per-tenant worker configurations
```

The codebase follows a **layered architecture**:

- `domain` — pure business logic, zero framework dependencies
- `application` — use cases that orchestrate domain objects
- `infrastructure` — external systems: Redis, Celery, logging
- `api` — thin HTTP adapter into the application layer

---

## Key Design Decisions

**Async by default** — LLM inference is slow. Celery decouples HTTP from processing so the API stays responsive regardless of model latency.

**LLM provider agnostic** — The `LLMAgentFactory` accepts any LLM via dependency injection. Swapping Ollama for OpenAI or another provider requires no changes to agent or task logic.

**Explicit task chaining** — Tasks are chained via `task.context = [previous_task]`. `Crew.kickoff()` respects task-agent assignment and passes each task's `result` as context to downstream tasks. This gives full control over the agent pipeline without relying on any framework magic.

**Per-tenant queue routing** — Tasks are dispatched via `run_crew.delay(tenant_id, payload)`. Each worker pod is started with `TENANT_ID` as an env var and listens exclusively on `crewai.<TENANT_ID>`. Tenant A's workload never delays tenant B.

**Sync escape hatch** — `/domain/run-sync` runs the crew synchronously, bypassing Celery entirely. No Redis or worker needed — useful for local development and debugging.

**Deployment parity** — The same application code runs locally, in Docker Compose, and on Kubernetes. Only `k8crew.env` changes between environments.

---

## Requirements

- Python 3.11+
- Poetry
- Docker (for Docker Compose option)
- kubectl + a Kubernetes cluster (for K8s option)
- Ollama-compatible hardware (CPU works, GPU recommended for speed)

---