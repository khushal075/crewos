"""Run configured agent tool dispatch with real embeddings and disposable data.

Run as a separate CLI process: environment overrides apply only to this process.
No chat-model decision loop or API authentication is exercised.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=["nomic-embed-text:v1.5"])
    parser.add_argument("--revision", required=True)
    parser.add_argument("--dimensions", type=int, required=True)
    parser.add_argument("--base-url", default="http://localhost:11434")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="crewos-hybrid-agent-") as directory:
        os.environ.update(
            RETRIEVAL_STRATEGY="hybrid",
            HYBRID_CANDIDATE_K="20",
            HYBRID_RRF_RANK_CONSTANT="60",
            DOCUMENT_DB_PATH=str(Path(directory) / "source.db"),
            EMBEDDING_DB_PATH=str(Path(directory) / "cache.db"),
            EMBEDDING_MODEL=args.model,
            EMBEDDING_MODEL_REVISION=args.revision,
            EMBEDDING_DIMENSIONS=str(args.dimensions),
            EMBEDDING_BASE_URL=args.base_url,
            EMBEDDING_DOCUMENT_PREFIX="search_document: ",
            EMBEDDING_QUERY_PREFIX="search_query: ",
        )
        # Settings are first loaded after the temporary process environment is ready.
        from crewos.application.dtos.tool_execution_context import ToolExecutionContext
        from crewos.application.services.policy_executor import PolicyDeniedError
        from crewos.core.document_setup import (
            get_document_store,
            get_document_facade,
            get_document_executor,
        )
        from crewos.core.embedding_setup import build_embedding_space, build_text_embedding_provider
        from crewos.core.tool_setup import build_tool_dispatcher
        from crewos.ingestion import DocumentIngestionRequest
        from crewos.retrieval import RetrievalRequest, build_bm25_retrieval, build_vector_retrieval
        from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
        from crewos.retrieval.vector_demo import DOCUMENTS, QUERIES

        # Keep the machine-readable report on stdout and operational logs on stderr.
        for name in ("crewos.core.document_setup", "crewos.policy"):
            for handler in logging.getLogger(name).handlers:
                if isinstance(handler, logging.StreamHandler):
                    handler.setStream(sys.stderr)

        context = ToolExecutionContext(
            tenant_id="tenant_a",
            permissions=frozenset({"document.create", "document.search", "document.read"}),
        )
        source = get_document_store()
        facade = get_document_facade()
        documents = [
            facade.ingest(
                request=DocumentIngestionRequest(
                    content=text.encode(), title=f"Synthetic runbook {i}"
                ),
                context=context,
            )
            for i, text in enumerate(DOCUMENTS, 1)
        ]
        # Task-status tool is registered but unused, so no Redis reader is needed.
        dispatcher = build_tool_dispatcher(reader=None)
        executor = get_document_executor()
        space = build_embedding_space()
        provider = build_text_embedding_provider()
        keyword = build_bm25_retrieval(reader=source, executor=executor)
        vector = build_vector_retrieval(
            reader=source,
            executor=executor,
            provider=provider,
            store=SQLiteChunkEmbeddingStore(Path(directory) / "cache.db"),
            space=space,
        )
        cases = []
        for case_id, query, expected in QUERIES:
            start = monotonic()
            hybrid = dispatcher.dispatch(
                name="search_documents", arguments={"query": query, "top_k": 1}, context=context
            )
            elapsed = round((monotonic() - start) * 1000, 3)
            request = RetrievalRequest(query=query, top_k=1)
            bm25 = keyword.search(request=request, context=context)
            semantic = vector.search(request=request, context=context)
            target = documents[expected - 1].document_id
            cases.append(
                {
                    "case_id": case_id,
                    "hybrid_strategy": hybrid.strategy,
                    "hybrid_expected_at_1": bool(
                        hybrid.passages and hybrid.passages[0].document_id == target
                    ),
                    "bm25_expected_at_1": bool(
                        bm25.passages and bm25.passages[0].document_id == target
                    ),
                    "vector_expected_at_1": bool(
                        semantic.passages and semantic.passages[0].document_id == target
                    ),
                    "hybrid_elapsed_ms": elapsed,
                }
            )
        warm = []
        for _, query, _ in QUERIES:
            start = monotonic()
            dispatcher.dispatch(
                name="search_documents", arguments={"query": query, "top_k": 1}, context=context
            )
            warm.append(round((monotonic() - start) * 1000, 3))
        denied = False
        try:
            dispatcher.dispatch(
                name="search_documents",
                arguments={"query": "credentials"},
                context=ToolExecutionContext(tenant_id="tenant_a"),
            )
        except PolicyDeniedError:
            denied = True
        other = dispatcher.dispatch(
            name="search_documents",
            arguments={"query": "credentials"},
            context=context.model_copy(update={"tenant_id": "tenant_b"}),
        )
        passed = (
            all(
                c["hybrid_expected_at_1"] and c["hybrid_strategy"] == "hybrid_fusion_v1"
                for c in cases
            )
            and denied
            and not other.passages
        )
        report = {
            "check": "configured_hybrid_agent_tool_smoke",
            "passed": passed,
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "model_id": space.model_id,
            "revision": space.model_revision,
            "dimensions": space.dimensions,
            "revision_verification": provider.revision_verification,
            "case_count": len(cases),
            "cases": cases,
            "expected_at_1_counts": {
                name: sum(c[f"{name}_expected_at_1"] for c in cases)
                for name in ("hybrid", "bm25", "vector")
            },
            "warm_hybrid_elapsed_ms": warm,
            "permission_denial": denied,
            "other_tenant_empty": not other.passages,
            "cost": {
                "monetary_cost": None,
                "reason": "Local compute costs and provider token usage are not measured.",
            },
            "scope": "Three synthetic queries over three temporary one-chunk documents. Configured search_documents tool dispatch with trusted contexts; no model-decided agent loop, API authentication, representative benchmark or load test. Baseline vector searches share the cache; timings are not an algorithm speed comparison. Process environment overrides only; existing tenant data/configuration unchanged.",
        }
        print(json.dumps(report, indent=2))
        return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
