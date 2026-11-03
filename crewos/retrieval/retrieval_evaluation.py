"""Offline passage relevance metrics. Human labels are assumptions, not ground truth."""

from dataclasses import dataclass
from statistics import mean
from uuid import UUID

from crewos.retrieval.search_contracts import RetrievalResult

PassageIdentity = tuple[UUID, int, int]  # document ID, version, zero-based chunk index


@dataclass(frozen=True)
class QueryMetrics:
    recall_at_k: float
    reciprocal_rank_at_k: float


def evaluate_result(
    result: RetrievalResult, *, relevant: frozenset[PassageIdentity], k: int
) -> QueryMetrics:
    """Evaluate a query with at least one labeled relevant passage, within one tenant.

    Missing hits score zero. Errors must reach the caller rather than become no hits.
    Unlabeled passages count as irrelevant, so incomplete labels can distort scores.
    """
    if type(k) is not int or not 1 <= k <= 20:
        raise ValueError("k must be an integer between 1 and 20")
    if not relevant:
        raise ValueError("Recall evaluation requires nonempty relevance labels")
    identities = [
        (hit.document_id, hit.document_version, hit.chunk.index) for hit in result.passages[:k]
    ]
    first_rank = next((rank for rank, key in enumerate(identities, 1) if key in relevant), None)
    return QueryMetrics(
        recall_at_k=len(set(identities) & relevant) / len(relevant),
        reciprocal_rank_at_k=1 / first_rank if first_rank else 0.0,
    )


def summarize_queries(metrics: tuple[QueryMetrics, ...]) -> dict[str, float]:
    """Macro averages: each query has equal weight regardless of its label count."""
    if not metrics:
        raise ValueError("At least one evaluated query is required")
    return {
        "mean_recall_at_k": mean(item.recall_at_k for item in metrics),
        "mrr_at_k": mean(item.reciprocal_rank_at_k for item in metrics),
    }
