"""Run a synthetic, local-only baseline: python -m crewos.retrieval.evaluate_demo."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.chunking import ChunkingOptions, ChunkingRequest, build_character_chunking
from crewos.ingestion import DocumentInput, StoredDocumentVersion, build_sqlite_document_store
from crewos.retrieval import RetrievalRequest, build_keyword_retrieval, build_bm25_retrieval
from crewos.retrieval.retrieval_evaluation import evaluate_result, summarize_queries

# One passage per document keeps the initial relevance labels easy to inspect.
DOCUMENTS = (
    ("Webhook recovery", "Retry webhook timeout failures with exponential backoff."),
    ("Credentials", "Refresh expired credentials before reconnecting the CRM integration."),
    ("Duplicate events", "Use an idempotency key to prevent duplicate event processing."),
    ("Rate limits", "For HTTP 429 rate limits, respect Retry-After before sending again."),
)
# (case ID, query, relevant document numbers). Labels refer to version 1, chunk 0.
CASES = (
    ("literal_timeout", "webhook timeout", (1,)),
    ("literal_credentials", "expired credentials", (2,)),
    ("literal_duplicates", "duplicate event", (3,)),
    ("literal_rate_limit", "429 rate limits", (4,)),
    ("paraphrased_credentials", "renew authentication secrets", (2,)),
    ("paraphrased_duplicates", "avoid repeated execution", (3,)),
    ("two_recovery_sources", "timeout credentials", (1, 2)),
)


def run_demo(*, strategy: str = "keyword_overlap", k: int = 1) -> dict:
    """Use disposable SQLite; do not read application secrets or existing documents."""
    builders = {"keyword_overlap": build_keyword_retrieval, "bm25": build_bm25_retrieval}
    if strategy not in builders:
        raise ValueError("Unknown evaluation strategy")
    RetrievalRequest(query="Validate evaluation cutoff", top_k=k)
    with TemporaryDirectory(prefix="crewos-retrieval-eval-") as directory:
        store = build_sqlite_document_store(Path(directory) / "documents.sqlite3")
        for number, (title, text) in enumerate(DOCUMENTS, 1):
            store.create_version(
                StoredDocumentVersion(
                    tenant_id="evaluation",
                    document_id=UUID(int=number, version=4),
                    version=1,
                    document=DocumentInput(title=title, text=text),
                    chunking=build_character_chunking().chunk(
                        ChunkingRequest(
                            text=text, options=ChunkingOptions(chunk_size=500, overlap=0)
                        )
                    ),
                )
            )
        facade = builders[strategy](
            reader=store, executor=PolicyEnforcedExecutor(PermissionPolicyService())
        )
        # Synthetic fixture identity; real requests must use authenticated server context.
        context = ToolExecutionContext(
            tenant_id="evaluation", permissions=frozenset({"document.search", "document.read"})
        )
        rows, metrics = [], []
        for case_id, query, numbers in CASES:
            relevant = frozenset((UUID(int=n, version=4), 1, 0) for n in numbers)
            result = facade.search(request=RetrievalRequest(query=query, top_k=k), context=context)
            measured = evaluate_result(result, relevant=relevant, k=k)
            metrics.append(measured)
            rows.append(
                {
                    "case_id": case_id,
                    "query": query,
                    **asdict(measured),
                    "expected_document_numbers": list(numbers),
                    "retrieved_document_ids": [str(hit.document_id) for hit in result.passages],
                }
            )
    return {
        "strategy": result.strategy,
        "dataset": "synthetic_integration_runbooks_v1",
        "k": k,
        "chunk_size": 500,
        "overlap": 0,
        "queries": rows,
        **summarize_queries(tuple(metrics)),
    }


def run_comparison(*, k: int = 1) -> dict:
    """Keep the dataset, chunking, labels, and cutoff identical across strategies."""
    return {
        "reports": [run_demo(strategy=strategy, k=k) for strategy in ("keyword_overlap", "bm25")]
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", action="store_true", help="Evaluate overlap and BM25")
    parser.add_argument("--top-k", type=int, choices=range(1, 21), default=1)
    args = parser.parse_args()
    report = run_comparison(k=args.top_k) if args.compare else run_demo(k=args.top_k)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
