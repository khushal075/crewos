# Retrieval foundation and keyword baseline

The module provides contracts, a permission-enforcing facade, keyword-overlap and
BM25 strategies, a bounded current-tenant corpus reader, and offline evaluation.
Agent integration exposes search_documents and validates structured citation
provenance. There is no dedicated retrieval HTTP endpoint, embedding model,
or vector store yet. An explicit document-answer mode enforces a successful
search, structured source provenance and a separate model-based support review.
A positive review is a fallible assessment rather than proof of correctness.

```mermaid
flowchart LR
    Caller[Authenticated application caller] --> Facade[RetrievalFacade]
    Facade --> Policy[document.search and document.read checks]
    Policy --> Strategy[KeywordOverlapRetriever]
    Strategy --> Reader[Public DocumentCorpusReaderInterface]
    Reader --> SQLite[Ingestion-owned SQLite adapter]
    SQLite --> Current[Tenant-scoped latest snapshots]
    Current --> Rank[Distinct query-word overlap and stable tie ordering]
    Rank --> Result[Passages with exact document version and chunk positions]
```

## Responsibilities and boundaries

- `search_contracts.py`: query, top-k, source passage, and ordered result shapes.
- `passage_retriever.py`: replaceable strategy interface and result/capacity errors.
- `retrieval_facade.py`: shared policy enforcement, validation, top-k checks,
  and safe metadata-only lifecycle events.
- `strategies/keyword_overlap.py`: deterministic word-overlap ranking.
- `bootstrap.py`: wire the strategy/reader behind the public facade.
- Ingestion's `document_corpus_reader.py`: a narrow read boundary returning
  validated current document snapshots under trusted tenant scope.

Retrieval imports the public ingestion interface, not its adapter or tables.
The SQLite reader scopes and versions data before retrieval sees candidates.
Both search and content-read permission are required before corpus access.
The interface and Python module boundary are not an authentication mechanism or
security sandbox; callers must establish context and implementations must honor
scope. No extra grants are enabled by these changes.

## How the baseline works

Case-fold the query and each chunk; extract Unicode `\w+` terms (letters, digits,
underscores). Score a chunk by the count of distinct query terms present. Select
positive-scoring chunks, ordered by descending count, then document ID, version,
and chunk index. Return at most top-k, with the original text and source offsets.
No score is exposed as model confidence. Titles/source labels are metadata only.

For query `webhook timeout`, chunk A containing only `timeout` scores 1; chunk B
containing both terms scores 2. Repetition does not increase either score. There
is no stop-word removal, stemming, Unicode normalization beyond case folding,
accent folding, synonym expansion, phrase search, BM25 weighting, or semantic
reasoning. This is a learning baseline whose limitations should be measured.

## Admission and consistency

Defaults admit at most 20 latest documents and 8,000,000 stored snapshot bytes
per tenant scan; selected passage text is capped at 100,000 characters. Snapshot
bytes include document text, embedded chunks/overlap, and metadata. Limits are
operator-owned constructor settings. The SQLite reader computes scoped count
and byte total before decoding any snapshot. Excess fails explicitly, never
silently omitting documents. These limits make a small-corpus scan bounded;
they do not establish an indexed-search throughput guarantee.

One SQLite read transaction pins admission and returned rows to a consistent
snapshot. Historical versions are excluded even when they contain matching terms.
New queries after a committed delete see no deleted source; in-flight reads may
still reflect the earlier snapshot. No separate search index or stale index copy
exists in this baseline. Database and validation failures propagate as failures.

## Verification and next steps

Real SQLite tests verify score ordering, evidence identity, stable ties, tenant
filtering before admission/ranking, update/deletion visibility, read consistency
during a concurrent delete, admission before decoding, output limits, corruption
propagation, and rejection of a wrongly scoped custom reader. Controlled facade
tests verify permission gates and privacy of lifecycle logs. None of these tests
establish semantic relevance or enterprise search quality.

Next: evaluate this baseline with explicit queries/expected source passages,
then introduce indexed keyword search/BM25 and compare failure cases. Embeddings,
vector similarity, hybrid retrieval, reranking, and RAG generation follow as
separate learning and implementation steps.

## Reproducible offline relevance evaluation

Run `poetry run python -m crewos.retrieval.evaluate_demo` from the repository.
The runner creates disposable SQLite storage containing four synthetic integration
runbooks and executes seven explicitly labeled queries through the authorized facade.
It needs no model, Redis, credentials, network, or existing application database.
The fixture identity is synthetic; it does not implement request authentication.

The reviewable documents and labels live in `crewos/retrieval/evaluate_demo.py`.
Labels identify an exact document ID, version, and zero-based chunk index within the
evaluation tenant. Each document currently has one chunk (500 character window,
zero overlap). Changing source versions or chunking requires reviewing those labels.

- Recall@k: relevant passages returned in the first k results divided by all labeled
  relevant passages for that query.
- Reciprocal rank@k: 1 divided by the rank of the first relevant passage; zero if
  none occurs within k. MRR@k averages that value across queries.
- Mean recall gives each query equal weight, regardless of its number of labels.

With `keyword_overlap_v1` at k=1, mean recall is 0.642857 and MRR is 0.714286.
Four literal queries find their passage. Two paraphrases miss entirely. The query
`timeout credentials` finds one of two relevant sources, giving recall 0.5 but
reciprocal rank 1. These metrics answer different questions.

This is a small, hand-authored teaching fixture, not a held-out benchmark or evidence
of production retrieval quality. Unlabeled passages are treated as irrelevant;
incomplete or subjective labels affect the scores. Empty relevance sets are rejected
because recall would be undefined; no-answer behavior needs separate evaluation.
Execution failures propagate rather than being counted as empty results. Security,
latency, capacity, answer factuality, and citation support require separate checks.
Before comparing strategies, keep the corpus, labels, chunking and k fixed, and
expand the dataset with representative queries independent of ranking development.

## Abstractions for keyword, vector, and hybrid retrieval

`PassageRetrieverInterface` is the shared search Strategy contract. Keyword,
vector, and hybrid retrievers all accept a query and trusted tenant scope and
return the same evidence contract. Separate marker interfaces for each algorithm
family are unnecessary until their callers need different behavior.

`RankedResultFusionInterface` is a second Strategy contract, for combining ranked
lists. A future hybrid retriever will receive child retrievers and a fusion
implementation through dependency injection. Composition lets hybrid retrieval
itself satisfy the same search interface. No hybrid or fusion algorithm is
implemented by this abstraction-only step.

```mermaid
flowchart TD
    F[Authorized RetrievalFacade] --> S[PassageRetrieverInterface]
    S -. implementation .-> K[Keyword strategy]
    S -. future implementation .-> V[Vector strategy]
    S -. future implementation .-> H[Hybrid strategy]
    H -. composes .-> K
    H -. composes .-> V
    H -. uses .-> R[RankedResultFusionInterface]
```

`RankedResultFusionRequest` holds the original retrieval request and one to eight
ordered child results. Each result can be empty. The fusion implementation must
honor top_k, deduplicate exact source identities, reject conflicting evidence for
one identity, and document deterministic tie-breaking. The boundary uses ranks,
not scores whose meanings differ between search algorithms.

The composing retriever is responsible for supplying the same query and trusted
tenant to every child. Fusion does not authenticate, authorize, search, or prove
that passages are current. Separate child reads are not automatically a consistent
snapshot; hybrid implementation will need an explicit version/availability policy.
Existing facade authorization remains the public entry point.

## Concrete BM25 strategy

`build_bm25_retrieval(reader=..., executor=...)` injects `BM25Retriever` into the
same authorized facade. The overlap builder remains available. There is no
automatic runtime selection, hybrid search, vector backend, or index in this step.

Each current tenant chunk is one search unit. With N chunks, df chunks containing
term t, frequency tf in the current chunk, length dl, and mean length avgdl:

```text
idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
score = sum over distinct matching query terms of:
        idf(t) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
```

Defaults are k1=1.2 and b=0.75. k1 controls frequency saturation; b controls
length normalization (zero disables it). Configuration accepts finite
0 < k1 <= 100 and 0 <= b <= 1; the k1 upper bound is an application guardrail.
The positive IDF form is documented in
[Apache Lucene's BM25Similarity](https://lucene.apache.org/core/9_12_3/core/org/apache/lucene/search/similarities/BM25Similarity.html).
This implementation does not promise identical Lucene or SQLite FTS5 scores.

Tokenization remains Unicode word matching after case folding, with no stemming,
stopword removal, phrase handling, or synonym expansion. Query repetitions are
ignored. Chunk statistics include all current tenant chunks, including zero-word
chunks; an empty or all-zero-word corpus returns no hits. Overlapping chunks count
separately, so changing chunking changes statistics and rankings. Titles are not
searched. Positive scores rank highest; exact ties use document ID, version, then
chunk index. Scores are internal ordering signals, not correctness probabilities.

The bounded snapshot read and output character caps match the overlap strategy.
Statistics are rebuilt in memory on each request, suitable only for the admitted
small corpus. Security and storage failures propagate. No benchmark improvement
is claimed; the fixed-data comparison below reports the observed results.

## Comparing concrete keyword strategies

Run `poetry run python -m crewos.retrieval.evaluate_demo --compare`, optionally with
`--top-k 2`. Without `--compare`, the original overlap baseline remains the default.
Both runs seed identical disposable corpora with fixed document IDs, text, versions,
chunking, query labels and cutoff. Reports include actual ordered document IDs and
expected fixture document numbers so results can be inspected beyond aggregates.
Errors are propagated; no backend fallback is performed by the evaluation runner.

At k=1, both strategies score mean recall 0.642857 and MRR 0.714286 on the seven
queries. This dataset cannot demonstrate that BM25 improves retrieval quality.
Its literal matches are straightforward and its paraphrase misses require more
than changing keyword ranking. At k=2, both retrieve both expected passages for
`timeout credentials`. Cutoff changes and algorithm changes must be compared
separately. Focused BM25 tests cover rarity, length and frequency behavior; these
are correctness checks, not independent evidence of real-world relevance gains.
Representative, independently labeled queries remain necessary for that claim.

## Agent tool integration

```mermaid
flowchart TD
    A[Model proposes search_documents] --> D[ToolDispatcher validates RetrievalRequest]
    D --> H[SearchDocumentsHandler]
    H --> F[RetrievalFacade checks search and read permissions]
    F --> B[BM25Retriever]
    B --> L[ConfiguredDocumentCorpusReader]
    L --> S[Tenant-scoped SQLite snapshot]
    S --> O[Exact source passages]
    O --> H
    H --> T[Bounded JSON tool observation]
    T --> A
```

`core/tool_setup.py` registers the tool for the existing agent setup used by CLI
and worker execution. A facade can be injected for alternate composition/testing.
`core/retrieval_setup.py` supplies a lazy configured corpus reader, ensuring no
SQLite initialization or lookup before authorization. The application handler
translates the tool invocation to the public retrieval API and admits at most
32,000 serialized UTF-8 result bytes. Default retrieval allows 8,000 passage text
characters; other corpus limits remain unchanged. It does not truncate evidence.

Identity comes from authenticated CLI/API execution or the worker's current
server-assigned context, never the model arguments. Both document.search and
document.read are required; task-status permission is separate. Registration
does not grant access. Search is marked replay-safe because it has no document
mutation, not because results are immutable across retry attempts.

Tool text remains untrusted. Model instructions are one layer, not a security
boundary for authorization or a complete prompt-injection defense. Existing durable
checkpoints store tool observations and therefore may contain retrieved text.
Grounded generation, citation validation, and answer evaluation remain separate
work; scripted integration tests establish observation delivery and access control.

## Citation provenance in the agent lifecycle

`SourceReference` identifies document ID, version and chunk index. Optional
`FinalAnswerDecision.citations` are bounded and distinct. Only successful backend
`search_documents` results extend the run's observed-source set; model arguments,
caller-supplied tool history, and prose do not. The set is persisted with the
existing history checkpoint. Before accepting a final answer, the loop checks
that every structured citation belongs to that set. Resume of a final-answer
checkpoint applies the same check without invoking a model again.

The check fails closed on an unobserved identity. It does not attempt semantic
claim support, require citations, parse prose references, refresh document access,
or prevent prompt injection. A cited passage can still be irrelevant or wrong.
Empty citations preserve non-document tasks and existing result compatibility,
although serialized final results now include `citations: []`. Current CLI output
prints any references; background results retain them as structured data.

## Explicit document-answer contracts

`application/dtos/document_answer.py` defines a discriminated union: the `outcome`
field selects which shape is valid. `DocumentAnswerOutcome` enumerates the two
values. This is separate from general-purpose `FinalAnswerDecision`.

- `answered`: nonblank answer text and 1–20 distinct structured source references.
- `insufficient_evidence`: a nonblank explanation, with no answer or citations fields.

Zero search hits can motivate insufficient evidence, but nonempty search hits may
also be irrelevant, incomplete, outdated, or contradictory. The shape does not
judge any of those conditions. Authorization denials, storage failures, capacity
limits, and timeouts remain operational exceptions, not insufficient-evidence
outcomes. The explanation is a model proposal; its content is not validated by
Pydantic. An answered proposal still needs provenance and semantic support checks.

These contracts are integrated through the explicit document mode described below.
General mode remains separate. The application fixes the mode from the configured
CLI/API input and preserves it in the backend-created job and checkpoints. Model
output cannot switch to a general final answer during a document-mode run.

## Explicit document-mode execution

`AgentAnswerMode` selects `general` (default) or `document` at composition. API
submission copies that validated choice into the backend-created `AgentRunJob`.
`JsonDecisionAdapter` and `AgentLoop` use the same mode-specific decision union:

- General: `ToolCallDecision | FinalAnswerDecision`.
- Document: `ToolCallDecision | DocumentFinalAnswerDecision`, whose `result` is
  the discriminated `DocumentAnswer` contract.

The document loop accepts a final outcome only after successful backend search,
including a valid empty result. Answered outcomes must cite observed passages;
insufficient-evidence explanations may follow empty or nonempty results. Ordinary
final_answer proposals do not satisfy the document schema and can consume the
existing bounded malformed-decision recovery allowance. Backend workflow/provenance
failures fail the run; they are not automatically rewritten as abstentions.

Checkpoints persist answer_mode and document_search_completed along with observed
sources and history. A conflicting mode on recovery is rejected. The worker
validates a checkpointed final result using the job's mode and repeats workflow
checks before finalization. Exact job payload matching prevents delivery-side mode
changes. Default general mode is omitted by the job serializer, preserving legacy
payload matching; explicit document mode is stored and compared for idempotency.
CLI rendering and background serialization preserve the two answer outcomes.

This is a workflow gate, not semantic validation. A model may choose inadequate
queries, cite irrelevant observed passages, or produce an insufficient-evidence
explanation with unsupported content. Claim support, retrieval coverage, ambiguity,
and abstention quality need separate evaluation. Current source availability and
grants are not refreshed at finalization; references identify observed evidence.


## Document-answer semantic review

Explicit document mode now includes a separate, replaceable evidence reviewer
before final acceptance. Earlier provenance/workflow rules remain necessary but
are not sufficient alone. The reviewer assesses claim support, citation coverage,
question relevance, conflicts and abstention; deterministic validation checks the
verdict and exact quoted evidence. Recovery requires a review receipt bound to
the question, answer and observed passage snapshots. General mode is unchanged.
See [the complete workflow and calibration guide](../evaluation/document-answer-evaluation.md)
for acceptance criteria, budgets, tests, live commands and model-judge limitations.

## Embedding boundary (contracts only)

`embedding_contracts.py` defines immutable `TextEmbeddingRequest` and
`EmbeddingBatch`; `text_embedding.py` defines `TextEmbeddingInterface`. The Ollama
provider adapter is described below. Embedding storage, vector search and retrieval
runtime wiring are not implemented yet.
Existing keyword/BM25 behavior is unchanged.

Requests preserve exact text and admit 1–128 nonblank inputs, at most 8,192
characters each and 65,536 characters total. These are application admission
limits, not token guarantees; adapters must also enforce provider limits. Results
contain model identity/revision, 1–16,384 dimensions and ordered finite numeric
vectors. Booleans and numeric strings are rejected. Adapters and callers must
use `validate_for(request)` to check output count. Shape validation cannot prove
that a provider preserved ordering or generated meaningful representations.

Corpus and query embeddings must share compatible configuration, including model
revision, preprocessing, task modes and normalization; equal dimensions do not
prove compatibility. A later storage contract must preserve this configuration.
Authorization must happen before sending source text to a provider. No permission
grants, network calls, provider credentials or logging of text/vectors are added.

## Native Ollama embedding adapter

`third_party/llm/ollama_embedding_adapter.py` implements the embedding protocol
without inheriting from it. `core/embedding_setup.py` composes it only when called
explicitly; existing agent search remains BM25. No new dependency is required.

Configure `EMBEDDING_BASE_URL` as a native HTTP(S) origin, without `/v1`, credentials,
query parameters or a path. Set `EMBEDDING_MODEL` to the exact installed model tag,
`EMBEDDING_MODEL_REVISION` to its inspected digest and `EMBEDDING_DIMENSIONS` to its
verified output length. No chat model is substituted and no model is downloaded.
Revision is operator-supplied provenance: this adapter does not verify the installed
digest or prevent a mutable tag changing. Pin/verify model artifacts before building
persistent indexes; equal names or dimensions alone do not prove compatibility.

The adapter posts ordered texts to `/api/embed` with `truncate=false`, so an input
that exceeds the model context should fail rather than silently lose content. It
checks the returned model label, finite vectors, configured dimensions and output
count. It does not request reduced dimensions or add task-specific prefixes. Model
selection must account for any required query/document formatting.

HTTP/transport errors and invalid results raise `EmbeddingProviderError`; there is
no automatic retry, fallback or empty-result substitution. Error messages omit
provider bodies, source text and vectors. Responses are admitted up to 32 MB by
default. Redirects and environment proxies are disabled to avoid silently forwarding
text elsewhere. Timeout is a per-socket operation bound, not a total request deadline;
a slow streaming response or DNS resolution can exceed it. Runtime deadline and usage
budget integration remain future work before this adapter is exposed as an agent tool.
Remote authentication and proxy support are not implemented.

Tests use controlled responses, requiring no model or credentials. Inspection of
local Ollama on 2026-11-05 found only completion models, so no live embedding-quality
or model inference claim is made. A dedicated embedding model is needed for the
next live check. Native API reference: https://docs.ollama.com/api/embed.

## Explicit live embedding smoke command

`poetry run python -m crewos.retrieval.embedding_demo --help` describes an opt-in
smoke runner. It sends two fixed synthetic integration examples, one with a nomic
`search_document:` prefix and one with `search_query:`. These prefixes are model-
specific and are not automatically applied by the generic provider adapter.

1. Install the dedicated model with `ollama pull nomic-embed-text:v1.5`.
2. Inspect `curl http://localhost:11434/api/tags` for the exact installed tag and digest.
3. Inspect model information using `/api/show` to establish the expected dimensions.
4. Run the command below, substituting the inspected values:

```bash
poetry run python -m crewos.retrieval.embedding_demo \
  --model nomic-embed-text:v1.5 \
  --revision INSTALLED_MODEL_DIGEST \
  --dimensions VERIFIED_DIMENSIONS
```

The runner prints a JSON summary with counts, dimensions, configured revision and
actual elapsed time, without printing vectors. It requires finite nonzero norms
and one vector per input. It does not measure semantic relevance, verify the digest,
modify `.env.local`, store vectors, change retrieval or authenticate a real tenant.
Failures return exit code 1. Tests use controlled providers and do not download models.

On 2026-11-05 the installation attempt failed because the Ollama registry connection
was refused. At that point no dedicated embedding model was installed and no successful live
inference report was produced. The subsequent successful check is recorded below.
Model reference: https://ollama.com/library/nomic-embed-text.

## Successful live inference

A subsequent local inspection on 2026-11-05 found `nomic-embed-text:v1.5` installed
with digest `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`,
embedding capability and 768 output dimensions. The real adapter smoke command
returned two finite nonzero 768-dimensional vectors in 1.153174 seconds. See
[the actual JSON report](../evaluation/reports/2026-11-05-embedding-smoke.json).
The digest was inspected separately via `/api/tags`; the adapter itself still does
not verify it. This is one inference smoke check, not a latency benchmark or relevance
evaluation. It does not exercise tenant authentication, indexing or vector retrieval.

## Vector comparison

`vector_similarity.py` defines `VectorSimilarityInterface` and
`strategies/cosine_similarity.py` provides `CosineSimilarity`. The strategy measures
vector direction: identical direction scores 1, perpendicular direction 0 and opposite
direction -1. Negative scores do not prove semantic contradiction, and no score is
an answer-correctness probability. Callers must enforce compatible embedding spaces
and tenant scope before comparing vectors.

Coordinates are validated as finite numeric values, excluding booleans and strings.
Empty/zero vectors, dimensions above 16,384 and mismatched lengths fail explicitly.
Normalization scales coordinates before computing vector length to avoid overflow
or underflow for extreme finite values. Tests cover known angles, magnitude
invariance, symmetry, extremes and rejection cases. This step does not store vectors
or change BM25 search; vector persistence and tenant-scoped retrieval remain next.
