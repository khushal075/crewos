"""Retrieve a wider pool, rerank, and revalidate current source evidence."""

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.ingestion import DocumentNotFound, DocumentStoreInterface, StoredDocumentVersion
from crewos.retrieval.document_embedding_indexer import guard_embedding_work
from crewos.retrieval.passage_reranker import PassageRerankerInterface, PassageRerankRequest
from crewos.retrieval.passage_retriever import (
    PassageRetrieverInterface,
    RetrievalCapacityExceeded,
    RetrievalResultError,
)
from crewos.retrieval.search_contracts import RetrievalRequest, RetrievalResult, RetrievedPassage


class CurrentPassageValidator:
    """Point lookups only; never scans the whole tenant corpus."""

    def __init__(self, source: DocumentStoreInterface):
        self._source = source

    def validate(self, *, tenant_id: str, passages: tuple[RetrievedPassage, ...]):
        documents = {}
        for passage in passages:
            if passage.document_id not in documents:
                try:
                    doc = self._source.get(tenant_id=tenant_id, document_id=passage.document_id)
                except DocumentNotFound:
                    raise RetrievalResultError(
                        "Reranking source disappeared; retry explicitly"
                    ) from None
                documents[passage.document_id] = StoredDocumentVersion.model_validate_json(
                    doc.model_dump_json()
                )
            doc = documents[passage.document_id]
            index = passage.chunk.index
            if (
                doc.tenant_id != tenant_id
                or doc.document_id != passage.document_id
                or doc.version != passage.document_version
                or index >= len(doc.chunking.chunks)
                or doc.chunking.chunks[index] != passage.chunk
                or doc.document.title != passage.title
                or doc.document.source_ref != passage.source_ref
            ):
                raise RetrievalResultError("Reranking evidence differs from current source")


class RerankingRetriever:
    def __init__(
        self,
        *,
        retriever: PassageRetrieverInterface,
        reranker: PassageRerankerInterface,
        validator: CurrentPassageValidator,
        candidate_k=20,
        max_output_chars=100_000,
    ):
        if (
            type(candidate_k) is not int
            or not 1 <= candidate_k <= 20
            or type(max_output_chars) is not int
            or max_output_chars < 1
        ):
            raise ValueError("Invalid reranking admission")
        self._retriever, self._reranker, self._validator = retriever, reranker, validator
        self._candidate_k, self._max_output = candidate_k, max_output_chars

    def search(self, *, request: RetrievalRequest, tenant_id: str) -> RetrievalResult:
        tenant_id = ToolExecutionContext(tenant_id=tenant_id).tenant_id
        request = RetrievalRequest.model_validate_json(request.model_dump_json())
        if request.top_k > self._candidate_k:
            raise RetrievalCapacityExceeded("Requested top_k exceeds reranking candidate depth")
        guard_embedding_work()
        candidates = self._retriever.search(
            request=request.model_copy(update={"top_k": self._candidate_k}), tenant_id=tenant_id
        )
        if not isinstance(candidates, RetrievalResult):
            raise RetrievalResultError("Reranking retriever returned an invalid type")
        candidates = RetrievalResult.model_validate_json(candidates.model_dump_json())
        if len(candidates.passages) > self._candidate_k:
            raise RetrievalResultError("Reranking candidate pool exceeded admission")
        if not candidates.passages:
            return RetrievalResult(strategy="reranked_retrieval_v1")
        self._validator.validate(tenant_id=tenant_id, passages=candidates.passages)
        result = self._reranker.rerank(
            request=PassageRerankRequest(request=request, candidates=candidates.passages)
        )
        if not isinstance(result, RetrievalResult):
            raise RetrievalResultError("Reranker returned an invalid result type")
        result = RetrievalResult.model_validate_json(result.model_dump_json())
        if len(result.passages) > request.top_k or any(
            p not in candidates.passages for p in result.passages
        ):
            raise RetrievalResultError("Reranker changed evidence or exceeded output admission")
        if sum(len(p.chunk.text) for p in result.passages) > self._max_output:
            raise RetrievalCapacityExceeded("Reranking output exceeds character admission")
        # Check the whole pool, not only selected candidates, after the expensive model call.
        self._validator.validate(tenant_id=tenant_id, passages=candidates.passages)
        guard_embedding_work()
        return RetrievalResult(strategy="reranked_retrieval_v1", passages=result.passages)
