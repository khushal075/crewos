# Configured hybrid agent-tool verification

The 6 November 2026 [raw report](reports/2026-11-06-hybrid-agent-tool.json) records
real local embedding calls through the configured `search_documents` dispatcher.
It uses the same three synthetic queries/documents as the vector lifecycle smoke
check. This is wiring evidence, not representative relevance evaluation.

Run from the repository root:

```bash
poetry run python docs/evaluation/run_live_hybrid_smoke.py \
  --model nomic-embed-text:v1.5 \
  --revision 0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f \
  --dimensions 768
```

Ollama must already serve that model on localhost:11434, or supply `--base-url`.
The adapter validates the pinned digest before and after each embedding batch.
No model is downloaded. The script sets hybrid/model settings and disposable
source/vector paths only in its own process environment, before importing settings.
It leaves `.env.local`, existing tenant data and other running processes unchanged.
Operational logs go to stderr and the JSON report goes to stdout.

| Observation | Recorded result |
| --- | --- |
| Hybrid expected passage first | 3/3 |
| BM25 expected passage first | 2/3 |
| Vector expected passage first | 3/3 |
| Configured tool strategy | `hybrid_fusion_v1` for all cases |
| First hybrid search | 106.283 ms |
| Warm hybrid queries | 24.059, 25.067, 22.505 ms |
| Permission denial | Passed |
| Other tenant, with no documents | Empty |
| Monetary cost | Not measured |

Hybrid recovers the credentials query missed by BM25 in this fixture. Vector
retrieval already succeeds on all three; this run does not establish an advantage
over vector search. Baselines share the embedding cache and are not timed for an
algorithm performance comparison. These are single-run measurements, not latency
percentiles, load capacity or pricing estimates.

The tool is dispatched using explicit trusted synthetic context. The script does
not run a chat-model agent loop, authenticate an HTTP request, or demonstrate
model-selected tool usage. It fails if hybrid ranking/wiring, denial or tenant
scope checks fail. Unit/integration tests separately verify that denied requests
cannot construct a provider or open source/vector stores.

For a normal application process, explicitly configure:

```dotenv
RETRIEVAL_STRATEGY=hybrid
HYBRID_CANDIDATE_K=20
HYBRID_RRF_RANK_CONSTANT=60
EMBEDDING_MODEL=nomic-embed-text:v1.5
EMBEDDING_MODEL_REVISION=0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f
EMBEDDING_DIMENSIONS=768
EMBEDDING_DOCUMENT_PREFIX="search_document: "
EMBEDDING_QUERY_PREFIX="search_query: "
```

Also set the native embedding endpoint and shared database paths for that deployment.
Restart affected API/agent workers after changing settings. Existing tenant grants
still require `document.search` and `document.read`; selecting hybrid grants neither.
The default remains BM25 and this verification does not switch running processes.
