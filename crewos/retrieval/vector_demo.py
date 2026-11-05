"""Tiny synthetic live vector-search demonstration, not a relevance benchmark."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import UUID

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.chunking import ChunkingOptions, ChunkingRequest, build_character_chunking
from crewos.ingestion import DocumentInput, StoredDocumentVersion, build_sqlite_document_store
from crewos.retrieval import RetrievalRequest, build_bm25_retrieval, build_vector_retrieval
from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
from crewos.retrieval.embedding_storage_contracts import EmbeddingSpace
from crewos.retrieval.text_embedding import TextEmbeddingInterface
from crewos.third_party.llm.ollama_embedding_adapter import (
    OllamaEmbeddingConfig,
    OllamaEmbeddingProvider,
)

DOCUMENTS = (
    "An expired authentication token causes requests to fail. Renew the token.",
    "A network timeout can interrupt an outbound webhook. Check connectivity before retrying.",
    "The Python developer role requires backend engineering experience.",
)
QUERIES = (
    ("credentials", "Why are credentials rejected?", 1),
    ("delivery_delay", "What should we do when a webhook takes too long?", 2),
    ("python_role", "Which role needs Python backend skills?", 3),
)


class CountingProvider:
    def __init__(self, provider: TextEmbeddingInterface):
        self.provider, self.calls = provider, 0

    def embed(self, *, request):
        self.calls += 1
        return self.provider.embed(request=request)


def run_vector_demo(*, provider: TextEmbeddingInterface, space: EmbeddingSpace) -> dict:
    counted = CountingProvider(provider)
    started = monotonic()
    with TemporaryDirectory(prefix="crewos-vector-demo-") as directory:
        source = build_sqlite_document_store(Path(directory) / "documents.sqlite3")
        cache = SQLiteChunkEmbeddingStore(Path(directory) / "embeddings.sqlite3")
        for number, text in enumerate(DOCUMENTS, start=1):
            source.create_version(
                StoredDocumentVersion(
                    tenant_id="synthetic_tenant",
                    document_id=UUID(int=number, version=4),
                    version=1,
                    document=DocumentInput(title=f"Synthetic runbook {number}", text=text),
                    chunking=build_character_chunking().chunk(
                        ChunkingRequest(
                            text=text, options=ChunkingOptions(chunk_size=500, overlap=0)
                        )
                    ),
                )
            )
        executor = PolicyEnforcedExecutor(PermissionPolicyService())
        vector = build_vector_retrieval(
            reader=source, executor=executor, provider=counted, store=cache, space=space
        )
        keyword = build_bm25_retrieval(reader=source, executor=executor)
        context = ToolExecutionContext(
            tenant_id="synthetic_tenant",
            permissions=frozenset({"document.search", "document.read"}),
        )
        cases = []
        for name, query, expected in QUERIES:
            request = RetrievalRequest(query=query, top_k=1)
            result = vector.search(request=request, context=context)
            baseline = keyword.search(request=request, context=context)
            expected_id = UUID(int=expected, version=4)
            cases.append(
                {
                    "case_id": name,
                    "expected_document_id": str(expected_id),
                    "vector_document_ids": [str(p.document_id) for p in result.passages],
                    "bm25_document_ids": [str(p.document_id) for p in baseline.passages],
                    "vector_expected_at_1": bool(
                        result.passages and result.passages[0].document_id == expected_id
                    ),
                }
            )
        cold_calls = counted.calls
        for _, query, _ in QUERIES:
            vector.search(request=RetrievalRequest(query=query, top_k=1), context=context)
        cached_calls = counted.calls - cold_calls
        if cached_calls != len(QUERIES):
            raise ValueError("Warm queries unexpectedly regenerated source embeddings")
    return {
        "check": "synthetic_vector_retrieval_demo",
        "model_id": space.model_id,
        "configured_revision": space.model_revision,
        "revision_verification": getattr(provider, "revision_verification", "not_reported"),
        "space_key": space.key,
        "dimensions": space.dimensions,
        "case_count": len(cases),
        "vector_expected_at_1_count": sum(case["vector_expected_at_1"] for case in cases),
        "cold_provider_calls": cold_calls,
        "warm_provider_calls": cached_calls,
        "elapsed_seconds": round(monotonic() - started, 6),
        "cases": cases,
        "scope": "Three synthetic development cases; not representative relevance or throughput evaluation. Uses trusted synthetic context, not request authentication.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--dimensions", type=int, required=True)
    args = parser.parse_args(argv)
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
    # This example intentionally uses nomic prefixes; other models need their own preparation.
    report = run_vector_demo(provider=OllamaEmbeddingProvider(config=config), space=space)
    print(json.dumps(report, indent=2))
    return 0 if report["vector_expected_at_1_count"] == report["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
