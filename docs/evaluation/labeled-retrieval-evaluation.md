# Labeled retrieval comparison

## Dataset and purpose

[Dataset](datasets/integration_incidents_v1.json) contains **17 short documents and
27 queries** about integration incidents: 8 development queries and 19 held-out
queries. The held-out split has 15 answerable queries and 4 unsupported queries.
The two tenants have separate source scope. Categories include paraphrases,
authentication versus permission failures, retries, multi-source questions, task
status, source deletion, and cross-tenant requests.

These are developer-authored synthetic judgments, not customer logs, independent
human assessments, or proof of production representativeness. They broaden the
previous three friendly smoke queries. Before release decisions, domain reviewers
should validate grades and add realistic corpus/query distributions, long documents,
chunk-level labels, larger tenant corpora and adversarial cases. Do not tune settings
against held-out results and then continue to call the same results held-out evidence.

Each document deliberately fits one chunk; grades refer to that document's version
1/chunk 0. Grade 3 is direct evidence, 2 useful supporting evidence, and 1 useful
background. Unlabeled passages count as irrelevant. Empty labels indicate an
unsupported question, not a provider error. Labels must point to existing same-tenant
documents. Dataset IDs, source versions and exact evidence are validated; failures
are reported explicitly instead of becoming fabricated empty results.

## Reproduce

Offline indexed/scan keyword comparison:

```bash
poetry run python -m crewos.evaluation.evaluate_retrieval \
  --dataset docs/evaluation/datasets/integration_incidents_v1.json \
  --split holdout --top-k 3 --progress
```

Five-strategy comparison using already-installed local models:

```bash
poetry run python -m crewos.evaluation.evaluate_retrieval \
  --dataset docs/evaluation/datasets/integration_incidents_v1.json \
  --split holdout --top-k 3 --progress \
  --embedding-model nomic-embed-text:v1.5 \
  --embedding-revision 0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f \
  --rerank-model qwen2.5-coder:14b-instruct-q4_K_M
```

Use `--strategies fts5_index` to evaluate the index alone, including larger corpora
that intentionally exceed scan-strategy capacity. Requested strategies with missing
model configuration fail explicitly. Optional endpoint flags select the native
embedding origin and OpenAI-compatible chat endpoint. This runner uses dummy local
authentication, not an external paid-provider credential. It never downloads a model.
It uses temporary databases and explicit trusted contexts, leaving existing data and
`.env.local` unchanged. Safe per-case progress goes to stderr; JSON goes to stdout.

The report records dataset hash, split, cutoff, model labels, embedding revision,
per-query metrics/errors, and observed latency. The embedding adapter verifies its
pinned digest pre/post inference. The reranking model is identified by its configured
tag; its package digest is not verified by this scorer. Scores and timing may change
with provider behavior and model versions. The command exits 1 if any query had an
execution error; low relevance is reported, not hidden behind an arbitrary success
threshold.

## Metrics

- **Recall@k:** fraction of all labeled relevant passages found in the first k.
- **MRR@k:** reciprocal rank of the first relevant passage, averaged across queries.
- **Precision@k:** relevant hits divided by k, even when fewer results were returned.
- **nDCG@k:** graded ranking quality, using gain `2^grade - 1` and a logarithmic rank
  discount, normalized against the ideal labeled ordering at the same cutoff.
- **Negative-hit rate:** fraction of successfully executed unsupported queries that
  returned any passage. This is retrieval behavior, not answer-level hallucination.
- **Errors:** separate counts/types. Answerable-query errors score zero in averages;
  unsupported-query errors are excluded from negative-hit rates, not counted as
  correct abstentions. Latency includes all attempted queries.

## Recorded run: 6 November 2026

[Full report](reports/2026-11-06-labeled-retrieval.json). All five strategies completed
19 held-out queries with **zero errors**. Positive relevance metrics average only
15 answerable queries. Negative-hit rates cover four unsupported queries.

| Strategy | Recall@3 | MRR@3 | nDCG@3 | Median latency (ms) |
| --- | ---: | ---: | ---: | ---: |
| Python BM25 scan | 0.856 | 0.933 | 0.873 | 0.449 |
| SQLite FTS5 index | 0.856 | 0.900 | 0.848 | 0.384 |
| Vector | 0.956 | 0.967 | 0.927 | 24.405 |
| Hybrid | 0.956 | 0.956 | 0.919 | 27.662 |
| Hybrid + model reranking | 0.978 | 1.000 | 0.996 | 6420.978 |

Reranking improved these authored labels but added substantial latency. Hybrid did
not outperform vector on every metric. FTS5 and the Python BM25 baseline have
different tokenization/scoring semantics, so identical rankings are not expected.
These numbers do not prove which strategy should be the deployment default.

**Negative-hit rate was 1.0 for every strategy.** All returned passages for all four
unsupported queries. Neither nearest-neighbor search nor reranking establishes that
an answer exists. The answer workflow must still evaluate evidence and abstain when
appropriate; this comparison does not execute answer generation/review.

The report's nearest-rank p95 is descriptive of only 19 samples, not a production
SLO estimate. Each strategy uses its own vector cache; first-use model/cache state
and fixed strategy order still affect timing. No throughput/concurrency benchmark
or monetary cost measurement was performed. Vector/hybrid strategies retain their
bounded scan limits; the keyword index test separately verifies a 101-document
corpus without full-source scan calls.
