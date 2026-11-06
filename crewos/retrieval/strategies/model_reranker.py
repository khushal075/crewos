"""Apply pair relevance scores without giving the model control over source content."""

import logging
from crewos.retrieval.passage_reranker import (
    PassageRerankRequest,
    PassageRelevanceScores,
    PassageScoringInterface,
)
from crewos.retrieval.passage_retriever import RetrievalResultError
from crewos.retrieval.search_contracts import RetrievalResult

logger = logging.getLogger(__name__)


class ModelPassageReranker:
    def __init__(self, scorer: PassageScoringInterface):
        self._scorer = scorer

    def rerank(self, *, request: PassageRerankRequest) -> RetrievalResult:
        request = PassageRerankRequest.model_validate_json(request.model_dump_json())
        if not request.candidates:
            return RetrievalResult(strategy="model_rerank_v1")
        scores = self._scorer.score(request=request)
        if not isinstance(scores, PassageRelevanceScores):
            raise RetrievalResultError("Reranker returned invalid scores")
        scores = PassageRelevanceScores.model_validate_json(scores.model_dump_json())
        if len(scores.scores) != len(request.candidates):
            raise RetrievalResultError("Reranker score count differs from candidate count")
        order = sorted(range(len(scores.scores)), key=lambda i: (-scores.scores[i], i))
        result = RetrievalResult(
            strategy="model_rerank_v1",
            passages=tuple(request.candidates[i] for i in order[: request.request.top_k]),
        )
        logger.info(
            {
                "event": "passages_reranked",
                "candidate_count": len(request.candidates),
                "passage_count": len(result.passages),
            }
        )
        return result
