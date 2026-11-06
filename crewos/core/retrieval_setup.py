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


class ConfiguredVectorRetriever:
    """Defer vector storage/configuration/provider setup until authorization succeeds."""

    def search(self, *, request, tenant_id):
        from crewos.core.config import settings
        from crewos.core.embedding_setup import build_embedding_space, build_text_embedding_provider
        from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
        from crewos.retrieval.document_embedding_indexer import DocumentEmbeddingIndexer
        from crewos.retrieval.strategies.vector import VectorRetriever

        space = build_embedding_space()
        provider = build_text_embedding_provider()
        store = SQLiteChunkEmbeddingStore(settings.EMBEDDING_DB_PATH)
        return VectorRetriever(
            ConfiguredDocumentCorpusReader(),
            indexer=DocumentEmbeddingIndexer(provider=provider, store=store, space=space),
            max_chunks=settings.VECTOR_MAX_CHUNKS,
            max_embedding_bytes=settings.VECTOR_MAX_EMBEDDING_BYTES,
            max_embedding_input_bytes=settings.VECTOR_MAX_INPUT_BYTES,
            max_output_chars=8_000,
        ).search(request=request, tenant_id=tenant_id)


class ConfiguredHybridRetriever:
    """Lazy composition inside the outer facade's authorization gate."""

    def search(self, *, request, tenant_id):
        from crewos.core.config import settings
        from crewos.core.embedding_setup import build_embedding_space, build_text_embedding_provider
        from crewos.retrieval import build_hybrid_retriever
        from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore

        space = build_embedding_space()
        provider = build_text_embedding_provider()
        store = SQLiteChunkEmbeddingStore(settings.EMBEDDING_DB_PATH)
        return build_hybrid_retriever(
            reader=ConfiguredDocumentCorpusReader(),
            provider=provider,
            store=store,
            space=space,
            candidate_k=settings.HYBRID_CANDIDATE_K,
            rank_constant=settings.HYBRID_RRF_RANK_CONSTANT,
            max_chunks=settings.VECTOR_MAX_CHUNKS,
            max_embedding_bytes=settings.VECTOR_MAX_EMBEDDING_BYTES,
            max_embedding_input_bytes=settings.VECTOR_MAX_INPUT_BYTES,
            max_output_chars=8_000,
        ).search(request=request, tenant_id=tenant_id)


def build_agent_retrieval() -> RetrievalFacade:
    from crewos.core.config import settings

    if settings.RETRIEVAL_STRATEGY == "hybrid":
        return RetrievalFacade(
            retriever=ConfiguredHybridRetriever(), executor=get_document_executor()
        )
    if settings.RETRIEVAL_STRATEGY == "vector":
        return RetrievalFacade(
            retriever=ConfiguredVectorRetriever(), executor=get_document_executor()
        )
    return build_bm25_retrieval(
        reader=ConfiguredDocumentCorpusReader(),
        executor=get_document_executor(),
        max_output_chars=8_000,
    )
