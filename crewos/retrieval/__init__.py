"""Public retrieval contracts, authorization facade, and keyword baseline setup."""

from crewos.retrieval.search_contracts import RetrievalRequest, RetrievedPassage, RetrievalResult
from crewos.retrieval.passage_retriever import PassageRetrieverInterface, RetrievalResultError
from crewos.retrieval.retrieval_facade import RetrievalFacade
from crewos.retrieval.bootstrap import (
    build_keyword_retrieval,
    build_bm25_retrieval,
    build_vector_retrieval,
    build_hybrid_retrieval,
    build_hybrid_retriever,
)
from crewos.retrieval.passage_retriever import RetrievalCapacityExceeded

from crewos.retrieval.ranked_result_fusion import (
    RankedResultFusionInterface,
    RankedResultFusionRequest,
)

__all__ = [
    "build_hybrid_retrieval",
    "build_hybrid_retriever",
    "build_vector_retrieval",
    "build_bm25_retrieval",
    "RankedResultFusionInterface",
    "RankedResultFusionRequest",
    "build_keyword_retrieval",
    "RetrievalCapacityExceeded",
    "RetrievalRequest",
    "RetrievedPassage",
    "RetrievalResult",
    "PassageRetrieverInterface",
    "RetrievalResultError",
    "RetrievalFacade",
]
