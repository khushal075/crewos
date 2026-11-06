"""Reproducible synthetic live verification; never opens application data stores."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TypedDict

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyEnforcedExecutor, PolicyDeniedError
from crewos.ingestion import (
    DocumentDeleteRequest,
    DocumentIngestionRequest,
    DocumentUpdateRequest,
    build_plain_text_ingestion_facade,
    build_sqlite_document_store,
)
from crewos.retrieval import RetrievalRequest, build_vector_retrieval
from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
from crewos.retrieval.chunk_embedding_store import EmbeddingWriteFenced
from crewos.retrieval.embedding_storage_contracts import ChunkEmbeddingWriteBatch, EmbeddingSpace
from crewos.retrieval.text_embedding import TextEmbeddingInterface
from crewos.retrieval.vector_demo import DOCUMENTS, QUERIES
from crewos.third_party.llm.ollama_embedding_adapter import (
    OllamaEmbeddingConfig,
    OllamaEmbeddingProvider,
)


class RelevanceCase(TypedDict):
    case_id: str
    expected_at_1: bool


class MeasuredProvider:
    """Record workload and wall time, excluding source text, vectors and credentials."""

    def __init__(self, provider):
        self.provider = provider
        self.phase = "cold"
        self.calls = []

    def embed(self, *, request):
        started = monotonic()
        result = self.provider.embed(request=request)
        self.calls.append(
            {
                "phase": self.phase,
                "input_text_count": len(request.texts),
                "input_utf8_bytes": sum(len(text.encode("utf-8")) for text in request.texts),
                "elapsed_ms": round((monotonic() - started) * 1000, 3),
            }
        )
        return result


def _cleanup(source_path, cache_path):
    env = dict(os.environ)
    env.update(
        DOCUMENT_DB_PATH=str(source_path),
        EMBEDDING_DB_PATH=str(cache_path),
        DOCUMENT_CLEANUP_TENANTS='["tenant_a"]',
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "crewos.workers.document_cleanup_worker",
            "--tenant-id",
            "tenant_a",
            "--once",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError("Isolated cleanup subprocess failed; no successful report produced")
    return json.loads(result.stdout.strip().splitlines()[-1])


def run_vector_lifecycle(*, provider: TextEmbeddingInterface, space: EmbeddingSpace) -> dict:
    measured = MeasuredProvider(provider)
    checks: dict[str, bool] = {}
    stages: dict[str, list[float]] = {}
    relevance: list[RelevanceCase] = []
    started = monotonic()
    with TemporaryDirectory(prefix="crewos-vector-lifecycle-") as directory:
        source_path, cache_path = Path(directory) / "source.db", Path(directory) / "vectors.db"
        source = build_sqlite_document_store(source_path)
        cache = SQLiteChunkEmbeddingStore(cache_path)
        executor = PolicyEnforcedExecutor(PermissionPolicyService())
        context = ToolExecutionContext(
            tenant_id="tenant_a",
            permissions=frozenset(
                {
                    "document.create",
                    "document.update",
                    "document.delete",
                    "document.search",
                    "document.read",
                }
            ),
        )
        facade = build_plain_text_ingestion_facade(store=source, executor=executor)
        documents = [
            facade.ingest(
                request=DocumentIngestionRequest(
                    content=text.encode(), title=f"Synthetic runbook {i}"
                ),
                context=context,
            )
            for i, text in enumerate(DOCUMENTS, 1)
        ]

        def retrieval():
            return build_vector_retrieval(
                reader=source, executor=executor, provider=measured, store=cache, space=space
            )

        vector = retrieval()

        def search(query, *, top_k=1, ctx=context):
            begin = monotonic()
            result = vector.search(request=RetrievalRequest(query=query, top_k=top_k), context=ctx)
            stages.setdefault(measured.phase, []).append(round((monotonic() - begin) * 1000, 3))
            return result

        def records(document):
            return cache.read_document(
                tenant_id="tenant_a",
                document_id=document.document_id,
                version=document.version,
                space=space,
                max_records=128,
                max_snapshot_bytes=8_000_000,
            )

        for name, query, expected in QUERIES:
            result = search(query)
            hit = bool(
                result.passages
                and result.passages[0].document_id == documents[expected - 1].document_id
            )
            relevance.append({"case_id": name, "expected_at_1": hit})
        old_records = records(documents[0])
        checks["initial_vectors_persisted"] = bool(old_records)
        # Reopen both stores to verify persistent cache reuse across instances.
        source = build_sqlite_document_store(source_path)
        cache = SQLiteChunkEmbeddingStore(cache_path)
        vector = retrieval()
        measured.phase = "warm_reopened"
        before = len(measured.calls)
        for _, query, _ in QUERIES:
            search(query)
        checks["warm_search_only_embeds_queries"] = len(measured.calls) - before == len(QUERIES)

        measured.phase = "updated"
        updated = facade.update(
            request=DocumentUpdateRequest(
                document_id=documents[0].document_id,
                expected_version=1,
                content=b"Authentication credentials rejected? Rotate the expired token, then retry once.",
                title="Updated authentication runbook",
            ),
            context=context,
        )
        update_delivery = _cleanup(source_path, cache_path)
        checks["update_cleanup_completed"] = update_delivery["outcomes"].get("completed") == 1
        checks["old_vectors_removed"] = records(documents[0]) == ()
        result = search(QUERIES[0][1], top_k=20)
        target = [p for p in result.passages if p.document_id == updated.document_id]
        checks["search_returns_only_current_version"] = bool(target) and all(
            p.document_version == 2 for p in target
        )
        checks["updated_document_at_1"] = bool(
            result.passages and result.passages[0].document_id == updated.document_id
        )
        current_records = records(updated)
        checks["new_vectors_persisted"] = bool(current_records)
        try:
            cache.put(ChunkEmbeddingWriteBatch(records=old_records))
            checks["obsolete_write_fenced"] = False
        except EmbeddingWriteFenced:
            checks["obsolete_write_fenced"] = True

        measured.phase = "authorization"
        before = len(measured.calls)
        denied = False
        try:
            search(QUERIES[0][1], ctx=ToolExecutionContext(tenant_id="tenant_a"))
        except PolicyDeniedError:
            denied = True
        checks["denial_before_provider_call"] = denied and len(measured.calls) == before
        result = search(QUERIES[0][1], ctx=context.model_copy(update={"tenant_id": "tenant_b"}))
        checks["other_tenant_sees_no_passages"] = (
            not result.passages and len(measured.calls) == before
        )

        measured.phase = "deleted"
        facade.delete(
            request=DocumentDeleteRequest(document_id=updated.document_id), context=context
        )
        deletion_delivery = _cleanup(source_path, cache_path)
        checks["deletion_cleanup_completed"] = deletion_delivery["outcomes"].get("completed") == 1
        result = search(QUERIES[0][1], top_k=20)
        checks["deleted_document_not_searchable"] = all(
            p.document_id != updated.document_id for p in result.passages
        )
        cache = SQLiteChunkEmbeddingStore(cache_path)
        checks["deleted_vectors_absent_after_reopen"] = records(updated) == ()
        try:
            cache.put(ChunkEmbeddingWriteBatch(records=current_records))
            checks["deleted_write_fenced_after_reopen"] = False
        except EmbeddingWriteFenced:
            checks["deleted_write_fenced_after_reopen"] = True
        with sqlite3.connect(source_path) as db:
            checks["both_events_acknowledged"] = (
                db.execute(
                    "SELECT COUNT(*) FROM document_cleanup_outbox WHERE tenant_id='tenant_a' AND state='completed'"
                ).fetchone()[0]
                == 2
            )

    passed = all(checks.values()) and all(case["expected_at_1"] for case in relevance)
    return {
        "check": "synthetic_vector_lifecycle",
        "passed": passed,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": space.model_id,
        "revision": space.model_revision,
        "revision_verification": getattr(
            provider, "revision_verification", "fixture_or_unreported"
        ),
        "dimensions": space.dimensions,
        "space_key": space.key,
        "checks": checks,
        "relevance": {
            "case_count": len(relevance),
            "recall_at_1": sum(c["expected_at_1"] for c in relevance) / len(relevance),
            "mrr_at_1": sum(c["expected_at_1"] for c in relevance) / len(relevance),
            "cases": relevance,
        },
        "search_latency_ms_by_phase": stages,
        "embedding_work": {
            "successful_calls": len(measured.calls),
            "input_text_count": sum(c["input_text_count"] for c in measured.calls),
            "input_utf8_bytes": sum(c["input_utf8_bytes"] for c in measured.calls),
            "calls": measured.calls,
        },
        "cost": {
            "monetary_cost": None,
            "token_usage": None,
            "reason": "No embedding tariff or provider token usage is captured. UTF-8 bytes are workload, not tokens. Local compute and electricity costs are not measured.",
        },
        "elapsed_seconds": round(monotonic() - started, 6),
        "scope": "Three synthetic one-chunk documents, trusted contexts and temporary databases. Real cleanup CLI, but no HTTP authentication, continuous-poller availability, answer generation, concurrency load or representative relevance benchmark. Latencies include provider revision checks and may include model loading; warm means source-vector cache reuse, not cached query embeddings.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--dimensions", type=int, required=True)
    args = parser.parse_args(argv)
    if args.model != "nomic-embed-text:v1.5":
        parser.error(
            "This fixture uses nomic v1.5 prefixes; other models need explicit preparation"
        )
    config = OllamaEmbeddingConfig(
        base_url=args.base_url,
        model=args.model,
        model_revision=args.revision,
        dimensions=args.dimensions,
        timeout_seconds=60,
    )
    space = EmbeddingSpace(
        provider_id="ollama_native_v1",
        model_id=config.model,
        model_revision=config.model_revision,
        dimensions=config.dimensions,
        preprocessing_revision="identity_v1",
        document_prefix="search_document: ",
        query_prefix="search_query: ",
    )
    report = run_vector_lifecycle(provider=OllamaEmbeddingProvider(config=config), space=space)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
