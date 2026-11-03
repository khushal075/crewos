"""Compose the first search strategy behind the public authorization facade."""

from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.ingestion import DocumentCorpusReaderInterface
from crewos.retrieval.retrieval_facade import RetrievalFacade
from crewos.retrieval.strategies.keyword_overlap import KeywordOverlapRetriever


def build_keyword_retrieval(
    *,
    reader: DocumentCorpusReaderInterface,
    executor: PolicyEnforcedExecutor,
    max_documents: int = 20,
    max_snapshot_bytes: int = 8_000_000,
    max_output_chars: int = 100_000,
) -> RetrievalFacade:
    return RetrievalFacade(
        retriever=KeywordOverlapRetriever(
            reader,
            max_documents=max_documents,
            max_snapshot_bytes=max_snapshot_bytes,
            max_output_chars=max_output_chars,
        ),
        executor=executor,
    )
