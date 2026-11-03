"""Run a synthetic, local-only baseline: python -m crewos.retrieval.evaluate_demo."""

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
from crewos.retrieval import RetrievalRequest, build_keyword_retrieval
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


def run_demo() -> dict:
    """Use disposable SQLite; do not read application secrets or existing documents."""
    k = 1
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
        facade = build_keyword_retrieval(
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
            rows.append({"case_id": case_id, "query": query, **asdict(measured)})
    return {
        "strategy": "keyword_overlap_v1",
        "dataset": "synthetic_integration_runbooks_v1",
        "k": k,
        "chunk_size": 500,
        "overlap": 0,
        "queries": rows,
        **summarize_queries(tuple(metrics)),
    }


if __name__ == "__main__":
    print(json.dumps(run_demo(), indent=2))
