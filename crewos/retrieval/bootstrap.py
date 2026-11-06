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


def build_bm25_retrieval(
    *,
    reader: DocumentCorpusReaderInterface,
    executor: PolicyEnforcedExecutor,
    k1: float = 1.2,
    b: float = 0.75,
    max_documents: int = 20,
    max_snapshot_bytes: int = 8_000_000,
    max_output_chars: int = 100_000,
) -> RetrievalFacade:
    from crewos.retrieval.strategies.bm25 import BM25Retriever

    return RetrievalFacade(
        retriever=BM25Retriever(
            reader,
            k1=k1,
            b=b,
            max_documents=max_documents,
            max_snapshot_bytes=max_snapshot_bytes,
            max_output_chars=max_output_chars,
        ),
        executor=executor,
    )


def build_vector_retrieval(
    *, reader, executor, provider, store, space, **limits
) -> RetrievalFacade:
    """Compose exact vector search behind the existing dual-permission gate."""
    from crewos.retrieval.document_embedding_indexer import DocumentEmbeddingIndexer
    from crewos.retrieval.strategies.vector import VectorRetriever

    return RetrievalFacade(
        retriever=VectorRetriever(
            reader,
            indexer=DocumentEmbeddingIndexer(provider=provider, store=store, space=space),
            **limits,
        ),
        executor=executor,
    )


def build_hybrid_retriever(
    *,
    reader,
    provider,
    store,
    space,
    candidate_k: int = 20,
    rank_constant: int = 60,
    max_documents: int = 20,
    max_snapshot_bytes: int = 8_000_000,
    max_chunks: int = 512,
    max_output_chars: int = 100_000,
    max_embedding_bytes: int = 32_000_000,
    max_embedding_input_bytes: int = 262144,
):
    """BM25 then vector on a shared snapshot; RRF ties prefer BM25 appearance."""
    from crewos.retrieval.document_embedding_indexer import DocumentEmbeddingIndexer
    from crewos.retrieval.strategies.bm25 import BM25Retriever
    from crewos.retrieval.strategies.hybrid import HybridRetriever
    from crewos.retrieval.strategies.reciprocal_rank_fusion import ReciprocalRankFusion
    from crewos.retrieval.strategies.vector import VectorRetriever

    indexer = DocumentEmbeddingIndexer(provider=provider, store=store, space=space)

    def children(snapshot):
        common = dict(
            max_documents=max_documents,
            max_snapshot_bytes=max_snapshot_bytes,
            max_output_chars=max_output_chars,
        )
        return (
            BM25Retriever(snapshot, **common),
            VectorRetriever(
                snapshot,
                indexer=indexer,
                max_chunks=max_chunks,
                max_embedding_bytes=max_embedding_bytes,
                max_embedding_input_bytes=max_embedding_input_bytes,
                **common,
            ),
        )

    return HybridRetriever(
        reader=reader,
        children_factory=children,
        fusion=ReciprocalRankFusion(rank_constant=rank_constant),
        candidate_k=candidate_k,
        max_documents=max_documents,
        max_snapshot_bytes=max_snapshot_bytes,
        max_chunks=max_chunks,
        max_output_chars=max_output_chars,
    )


def build_hybrid_retrieval(
    *, reader, executor, provider, store, space, **limits
) -> RetrievalFacade:
    """Authorize before invoking the composed raw hybrid retriever."""
    return RetrievalFacade(
        retriever=build_hybrid_retriever(
            reader=reader, provider=provider, store=store, space=space, **limits
        ),
        executor=executor,
    )


def build_indexed_retrieval(*, index, executor, **limits) -> RetrievalFacade:
    from crewos.retrieval.strategies.indexed_bm25 import IndexedBM25Retriever

    return RetrievalFacade(retriever=IndexedBM25Retriever(index, **limits), executor=executor)


def build_reranked_retrieval(*, retriever, reranker, source, executor, **limits) -> RetrievalFacade:
    from crewos.retrieval.strategies.reranking import CurrentPassageValidator, RerankingRetriever

    return RetrievalFacade(
        retriever=RerankingRetriever(
            retriever=retriever,
            reranker=reranker,
            validator=CurrentPassageValidator(source),
            **limits,
        ),
        executor=executor,
    )
