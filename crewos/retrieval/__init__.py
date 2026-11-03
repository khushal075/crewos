"""Public retrieval contracts, authorization facade, and keyword baseline setup."""

from crewos.retrieval.search_contracts import RetrievalRequest, RetrievedPassage, RetrievalResult
from crewos.retrieval.passage_retriever import PassageRetrieverInterface, RetrievalResultError
from crewos.retrieval.retrieval_facade import RetrievalFacade
from crewos.retrieval.passage_retriever import RetrievalCapacityExceeded

__all__ = [
    "RetrievalCapacityExceeded",
    "RetrievalRequest",
    "RetrievedPassage",
    "RetrievalResult",
    "PassageRetrieverInterface",
    "RetrievalResultError",
    "RetrievalFacade",
]
