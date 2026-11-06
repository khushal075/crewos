"""Equal-weight rank fusion; no searching, model calls or authorization."""

from fractions import Fraction
import logging
from uuid import UUID

from crewos.retrieval.passage_retriever import RetrievalResultError
from crewos.retrieval.ranked_result_fusion import RankedResultFusionRequest
from crewos.retrieval.search_contracts import RetrievedPassage, RetrievalResult

logger = logging.getLogger(__name__)


class ReciprocalRankFusion:
    """Sum 1/(rank_constant + one-based rank) across the supplied lists.

    Exact-score ties preserve first appearance in stable child-list order, then
    rank order. Absent passages contribute zero; empty children are valid. Each
    child gets equal weight, even when strategy names repeat. The caller must
    avoid accidentally supplying the same retriever twice.
    """

    def __init__(self, *, rank_constant: int = 60):
        if type(rank_constant) is not int or not 1 <= rank_constant <= 10000:
            raise ValueError("RRF rank constant must be an integer between 1 and 10000")
        self._rank_constant = rank_constant

    def fuse(self, *, request: RankedResultFusionRequest) -> RetrievalResult:
        # Revalidate nested models, including instances constructed with model_copy.
        request = RankedResultFusionRequest.model_validate_json(request.model_dump_json())
        scores: dict[tuple[UUID, int, int], Fraction] = {}
        passages: dict[tuple[UUID, int, int], RetrievedPassage] = {}
        first_seen: dict[tuple[UUID, int, int], int] = {}
        for child in request.ranked_results:
            for rank, passage in enumerate(child.passages, 1):
                identity = (passage.document_id, passage.document_version, passage.chunk.index)
                if identity in passages and passages[identity] != passage:
                    raise RetrievalResultError("Conflicting passage payloads in fusion inputs")
                if identity not in passages:
                    passages[identity] = passage
                    first_seen[identity] = len(first_seen)
                    scores[identity] = Fraction(0)
                scores[identity] += Fraction(1, self._rank_constant + rank)
        ordered = sorted(scores, key=lambda key: (-scores[key], first_seen[key]))
        selected = tuple(passages[key] for key in ordered[: request.request.top_k])
        logger.info(
            {
                "event": "ranked_results_fused",
                "strategy": "reciprocal_rank_fusion_v1",
                "child_count": len(request.ranked_results),
                "candidate_count": len(scores),
                "passage_count": len(selected),
                "rank_constant": self._rank_constant,
            }
        )
        return RetrievalResult(strategy="reciprocal_rank_fusion_v1", passages=selected)
