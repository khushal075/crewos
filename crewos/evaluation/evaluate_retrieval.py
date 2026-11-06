"""Labeled retrieval comparison with graded metrics, errors and negative queries."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import sys
from math import ceil, log2
from pathlib import Path
from statistics import mean, median
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import UUID

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.chunking import ChunkingOptions, ChunkingRequest, build_character_chunking
from crewos.ingestion import DocumentInput, StoredDocumentVersion, build_sqlite_document_store
from crewos.evaluation.retrieval_dataset import RelevanceDataset
from crewos.retrieval import (
    RetrievalRequest,
    build_bm25_retrieval,
    build_indexed_retrieval,
    build_vector_retrieval,
    build_hybrid_retriever,
    build_reranked_retrieval,
)
from crewos.retrieval.retrieval_facade import RetrievalFacade
from crewos.retrieval.search_contracts import RetrievalResult


logger = logging.getLogger(__name__)


def graded_metrics(retrieved, grades, *, k):
    if type(k) is not int or not 1 <= k <= 20:
        raise ValueError("Metric k must be 1..20")
    ordered = tuple(retrieved[:k])
    if len(set(ordered)) != len(ordered):
        raise ValueError("Metrics require unique document identities")
    if not grades:
        return {
            "recall_at_k": None,
            "mrr_at_k": None,
            "ndcg_at_k": None,
            "precision_at_k": None,
            "negative_hit": bool(ordered),
        }
    found = [grades.get(key, 0) for key in ordered]
    first = next((i for i, g in enumerate(found, 1) if g), None)
    dcg = sum((2**g - 1) / log2(i + 1) for i, g in enumerate(found, 1))
    ideal = sum(
        (2**g - 1) / log2(i + 1) for i, g in enumerate(sorted(grades.values(), reverse=True)[:k], 1)
    )
    return {
        "recall_at_k": sum(bool(g) for g in found) / len(grades),
        "mrr_at_k": 1 / first if first else 0.0,
        "precision_at_k": sum(bool(g) for g in found) / k,
        "ndcg_at_k": dcg / ideal,
        "negative_hit": None,
    }


def evaluate_strategies(
    *, dataset: RelevanceDataset, strategies: dict, identities: dict, k=3, split="holdout"
):
    dataset = RelevanceDataset.model_validate_json(dataset.model_dump_json())
    RetrievalRequest(query="validate cutoff", top_k=k)
    if split not in ("all", "development", "holdout"):
        raise ValueError("Unknown evaluation split")
    queries = [q for q in dataset.queries if split == "all" or q.split == split]
    if not queries:
        raise ValueError("Selected split has no queries")
    reports = []
    allowed = {(identities[d.id], d.tenant_id): d for d in dataset.documents}
    reverse = {v: key for key, v in identities.items()}
    for name, strategy in strategies.items():
        rows = []
        for query in queries:
            context = ToolExecutionContext(
                tenant_id=query.tenant_id,
                permissions=frozenset({"document.search", "document.read"}),
            )
            start, error, doc_keys = monotonic(), None, []
            try:
                result = strategy.search(
                    request=RetrievalRequest(query=query.query, top_k=k), context=context
                )
                if not isinstance(result, RetrievalResult):
                    raise TypeError("Invalid retrieval result type")
                result = RetrievalResult.model_validate_json(result.model_dump_json())
                if len(result.passages) > k:
                    raise ValueError("Output exceeded cutoff")
                for p in result.passages:
                    doc = allowed.get((p.document_id, query.tenant_id))
                    if (
                        doc is None
                        or p.document_version != 1
                        or p.chunk.index != 0
                        or p.chunk.text != doc.text
                        or p.title != doc.title
                        or p.source_ref is not None
                    ):
                        raise ValueError("Output violated evaluation evidence scope")
                    doc_keys.append(reverse[p.document_id])
            except Exception as exc:
                error, doc_keys = type(exc).__name__, []
            logger.info(
                {
                    "event": "relevance_case_completed",
                    "strategy": name,
                    "case_id": query.id,
                    "error_type": error,
                    "elapsed_ms": round((monotonic() - start) * 1000, 3),
                }
            )
            values = graded_metrics(doc_keys, query.relevant, k=k)
            rows.append(
                {
                    "case_id": query.id,
                    "category": query.category,
                    "split": query.split,
                    "retrieved_document_keys": doc_keys,
                    "error_type": error,
                    "elapsed_ms": round((monotonic() - start) * 1000, 3),
                    **values,
                }
            )
        positive = [r for r in rows if r["recall_at_k"] is not None]
        negatives = [r for r in rows if r["negative_hit"] is not None and r["error_type"] is None]
        latency = sorted(r["elapsed_ms"] for r in rows)
        summary = {
            metric: mean(r[metric] for r in positive) if positive else None
            for metric in ("recall_at_k", "mrr_at_k", "ndcg_at_k", "precision_at_k")
        }
        reports.append(
            {
                "strategy": name,
                "query_count": len(rows),
                "answerable_query_count": len(positive),
                "negative_successful_query_count": len(negatives),
                "negative_hit_rate": (
                    mean(r["negative_hit"] for r in negatives) if negatives else None
                ),
                "error_count": sum(r["error_type"] is not None for r in rows),
                "latency_ms": {
                    "p50": median(latency),
                    "p95_nearest_rank": latency[ceil(0.95 * len(latency)) - 1],
                },
                "metrics": summary,
                "queries": rows,
            }
        )
    return {
        "dataset": dataset.name,
        "dataset_sha256": hashlib.sha256(dataset.model_dump_json().encode()).hexdigest(),
        "provenance": dataset.provenance,
        "split": split,
        "k": k,
        "reports": reports,
        "scope": "Developer labels, one chunk per document, disposable data. Errors are explicit and count as zero on answerable metrics; negative-hit rates exclude errors. Negative hits mean retrieval returned something, not that an answer was generated. Strategy order and model/cache state affect timing; not a load benchmark.",
    }


def run_comparison(
    *, dataset, k=3, split="holdout", provider=None, space=None, scorer=None, strategy_names=None
):
    from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
    from crewos.retrieval.strategies.model_reranker import ModelPassageReranker

    dataset = RelevanceDataset.model_validate_json(dataset.model_dump_json())
    with TemporaryDirectory(prefix="crewos-labeled-eval-") as directory:
        source = build_sqlite_document_store(Path(directory) / "source.db")
        identities = {doc.id: UUID(int=i, version=4) for i, doc in enumerate(dataset.documents, 1)}
        for doc in dataset.documents:
            source.create_version(
                StoredDocumentVersion(
                    tenant_id=doc.tenant_id,
                    document_id=identities[doc.id],
                    version=1,
                    document=DocumentInput(title=doc.title, text=doc.text),
                    chunking=build_character_chunking().chunk(
                        ChunkingRequest(
                            text=doc.text, options=ChunkingOptions(chunk_size=8192, overlap=0)
                        )
                    ),
                )
            )
        executor = PolicyEnforcedExecutor(PermissionPolicyService())
        strategies = {
            "bm25_scan": build_bm25_retrieval(reader=source, executor=executor),
            "fts5_index": build_indexed_retrieval(index=source, executor=executor),
        }
        if provider is not None and space is not None:
            strategies["vector"] = build_vector_retrieval(
                reader=source,
                executor=executor,
                provider=provider,
                space=space,
                store=SQLiteChunkEmbeddingStore(Path(directory) / "vector.db"),
            )
            hybrid = build_hybrid_retriever(
                reader=source,
                provider=provider,
                space=space,
                store=SQLiteChunkEmbeddingStore(Path(directory) / "hybrid.db"),
            )
            strategies["hybrid"] = RetrievalFacade(retriever=hybrid, executor=executor)
            if scorer is not None:
                rerank_base = build_hybrid_retriever(
                    reader=source,
                    provider=provider,
                    space=space,
                    store=SQLiteChunkEmbeddingStore(Path(directory) / "reranked.db"),
                )
                strategies["hybrid_reranked"] = build_reranked_retrieval(
                    retriever=rerank_base,
                    reranker=ModelPassageReranker(scorer),
                    source=source,
                    executor=executor,
                )
        elif scorer is not None:
            from crewos.retrieval.strategies.indexed_bm25 import IndexedBM25Retriever

            strategies["fts5_reranked"] = build_reranked_retrieval(
                retriever=IndexedBM25Retriever(source),
                reranker=ModelPassageReranker(scorer),
                source=source,
                executor=executor,
            )
        if strategy_names is not None:
            if not strategy_names or any(name not in strategies for name in strategy_names):
                raise ValueError(
                    "Requested evaluation strategy requires missing model configuration or is unknown"
                )
            strategies = {name: strategies[name] for name in strategy_names}
        return evaluate_strategies(
            dataset=dataset, strategies=strategies, identities=identities, k=k, split=split
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=["bm25_scan", "fts5_index", "vector", "hybrid", "hybrid_reranked", "fts5_reranked"],
    )
    parser.add_argument("--progress", action="store_true", help="Log safe case progress to stderr")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--top-k", default=3, type=int, choices=range(1, 21))
    parser.add_argument("--split", choices=["all", "development", "holdout"], default="holdout")
    parser.add_argument("--embedding-model")
    parser.add_argument("--embedding-revision")
    parser.add_argument("--embedding-dimensions", type=int, default=768)
    parser.add_argument("--embedding-base-url", default="http://localhost:11434")
    parser.add_argument("--rerank-model")
    parser.add_argument("--chat-base-url", default="http://localhost:11434/v1")
    args = parser.parse_args(argv)
    if args.progress:
        logger.setLevel(logging.INFO)
        logger.addHandler(logging.StreamHandler(sys.stderr))
    if args.dataset.stat().st_size > 2_000_000:
        parser.error("Dataset exceeds 2MB admission")
    dataset = RelevanceDataset.model_validate_json(args.dataset.read_text())
    provider, space, scorer = None, None, None
    if args.embedding_model:
        if args.embedding_model != "nomic-embed-text:v1.5" or not args.embedding_revision:
            parser.error("This runner requires pinned nomic v1.5 with its task prefixes")
        from crewos.third_party.llm.ollama_embedding_adapter import (
            OllamaEmbeddingConfig,
            OllamaEmbeddingProvider,
        )
        from crewos.retrieval.embedding_storage_contracts import EmbeddingSpace

        config = OllamaEmbeddingConfig(
            base_url=args.embedding_base_url,
            model=args.embedding_model,
            model_revision=args.embedding_revision,
            dimensions=args.embedding_dimensions,
            timeout_seconds=60,
        )
        provider = OllamaEmbeddingProvider(config=config)
        space = EmbeddingSpace(
            provider_id="ollama_native_v1",
            model_id=config.model,
            model_revision=config.model_revision,
            dimensions=config.dimensions,
            preprocessing_revision="identity_v1",
            document_prefix="search_document: ",
            query_prefix="search_query: ",
        )
    if args.rerank_model:
        from langchain_openai import ChatOpenAI
        from crewos.third_party.llm.passage_relevance_scorer import JsonPassageRelevanceScorer

        scorer = JsonPassageRelevanceScorer(
            ChatOpenAI(
                base_url=args.chat_base_url,
                api_key="local-evaluation",
                model=args.rerank_model,
                temperature=0,
                max_retries=0,
            ),
            timeout_seconds=120,
        )
    report = run_comparison(
        dataset=dataset,
        k=args.top_k,
        split=args.split,
        provider=provider,
        space=space,
        scorer=scorer,
        strategy_names=args.strategies,
    )
    report.update(
        recorded_at_utc=datetime.now(timezone.utc).isoformat(),
        embedding_model=args.embedding_model,
        embedding_revision=args.embedding_revision,
        rerank_model=args.rerank_model,
    )
    print(json.dumps(report, indent=2))
    return 1 if any(r["error_count"] for r in report["reports"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
