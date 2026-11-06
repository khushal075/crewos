"""Compose agent retrieval; defer document database access until after policy checks."""

from collections.abc import Callable
from crewos.retrieval import PassageRetrieverInterface

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


class ConfiguredIndexedRetriever:
    def search(self, *, request, tenant_id):
        from crewos.retrieval.strategies.indexed_bm25 import IndexedBM25Retriever

        return IndexedBM25Retriever(get_document_store(), max_output_chars=8_000).search(
            request=request, tenant_id=tenant_id
        )


class ConfiguredRerankingRetriever:
    def __init__(self, base):
        self._base = base

    def search(self, *, request, tenant_id):
        from crewos.core.config import settings
        from crewos.retrieval.strategies.model_reranker import ModelPassageReranker
        from crewos.retrieval.strategies.reranking import (
            CurrentPassageValidator,
            RerankingRetriever,
        )
        from crewos.third_party.llm.passage_relevance_scorer import JsonPassageRelevanceScorer
        from crewos.third_party.llm.ollama_adapter import OllamaLLMProvider

        if not settings.RERANKING_MODEL:
            raise ValueError("Explicitly configure a reranking model before enabling reranking")
        if (
            settings.RETRIEVAL_STRATEGY == "hybrid"
            and settings.RERANKING_CANDIDATE_K > settings.HYBRID_CANDIDATE_K
        ):
            raise ValueError("Reranking candidate depth exceeds hybrid admission")
        model = OllamaLLMProvider().get().bind(model=settings.RERANKING_MODEL)
        return RerankingRetriever(
            retriever=self._base,
            reranker=ModelPassageReranker(
                JsonPassageRelevanceScorer(model, timeout_seconds=settings.LLM_TIMEOUT)
            ),
            validator=CurrentPassageValidator(get_document_store()),
            candidate_k=settings.RERANKING_CANDIDATE_K,
            max_output_chars=8_000,
        ).search(request=request, tenant_id=tenant_id)


def build_agent_retrieval() -> RetrievalFacade:
    from crewos.core.config import settings
    from crewos.retrieval.strategies.bm25 import BM25Retriever

    factories: dict[str, Callable[[], PassageRetrieverInterface]] = {
        "hybrid": ConfiguredHybridRetriever,
        "vector": ConfiguredVectorRetriever,
        "indexed_bm25": ConfiguredIndexedRetriever,
    }
    base = factories.get(settings.RETRIEVAL_STRATEGY)
    retriever: PassageRetrieverInterface = (
        base() if base else BM25Retriever(ConfiguredDocumentCorpusReader(), max_output_chars=8_000)
    )
    if settings.RERANKING_ENABLED:
        retriever = ConfiguredRerankingRetriever(retriever)
    return RetrievalFacade(retriever=retriever, executor=get_document_executor())
