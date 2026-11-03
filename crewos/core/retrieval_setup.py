"""Compose agent retrieval; defer document database access until after policy checks."""

from crewos.core.document_setup import get_document_executor, get_document_store
from crewos.ingestion import StoredDocumentVersion
from crewos.retrieval import RetrievalFacade, build_bm25_retrieval


class ConfiguredDocumentCorpusReader:
    def read_latest_documents(
        self, *, tenant_id: str, max_documents: int, max_snapshot_bytes: int
    ) -> tuple[StoredDocumentVersion, ...]:
        return get_document_store().read_latest_documents(
            tenant_id=tenant_id,
            max_documents=max_documents,
            max_snapshot_bytes=max_snapshot_bytes,
        )


def build_agent_retrieval() -> RetrievalFacade:
    return build_bm25_retrieval(
        reader=ConfiguredDocumentCorpusReader(),
        executor=get_document_executor(),
        max_output_chars=8_000,
    )
