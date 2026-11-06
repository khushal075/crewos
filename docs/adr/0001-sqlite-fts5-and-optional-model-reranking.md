# ADR 0001: transactional FTS5 keyword indexing and optional model reranking

Status: implemented in the current uncommitted change set.

## Context

Bounded BM25/vector scans and three-query smoke tests do not provide an indexed
keyword path, second-stage relevance judgments, or diverse labeled comparisons.
CrewOS currently targets one host and already stores immutable sources in SQLite.

## Decision

Keep replaceable index/scoring/reranking interfaces in the retrieval module. Add
per-tenant FTS5 keyword tables to the source database and maintain them inside source
write/delete/job transactions. Add optional structured model scoring over a bounded
candidate pool, preserving source evidence in backend code. Keep authorization
outside both adapters. Add a versioned labeled dataset and evaluation CLI with
explicit errors, unsupported-query behavior and recorded model results.

## Consequences

No separate search daemon or index-publication race is introduced for keyword search.
Tenant tables isolate corpus statistics. Schema 5 migration/rebuild needs a SQLite
write transaction and FTS5 support. Table count and single-writer contention limit
this design at larger scale; a distributed index can replace the interface later.

Model reranking adds latency and another model cost/budget boundary. It is disabled
by default. Valid scores do not prove relevance or answerability. Current-source
revalidation rejects observed races. Vector ANN indexing, multi-host operation,
calibrated answerability thresholds and production relevance labeling are not
established by this decision.

## Alternatives

An external search service offers independent scaling but adds deployment, index
publication and operational recovery work. An ANN vector backend addresses a different
bottleneck and remains a future adapter. A cross-encoder scorer could replace the
JSON model scorer without changing source contracts or the authorization boundary.
