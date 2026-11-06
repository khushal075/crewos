"""Compose bounded retrievers over one tenant snapshot, then verify source currency."""

from collections.abc import Callable
import logging

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.ingestion import DocumentCorpusReaderInterface, StoredDocumentVersion
from crewos.retrieval.document_embedding_indexer import guard_embedding_work
from crewos.retrieval.passage_retriever import (
    PassageRetrieverInterface,
    RetrievalCapacityExceeded,
    RetrievalResultError,
)
from crewos.retrieval.ranked_result_fusion import (
    RankedResultFusionInterface,
    RankedResultFusionRequest,
)
from crewos.retrieval.search_contracts import RetrievedPassage, RetrievalRequest, RetrievalResult

logger = logging.getLogger(__name__)


class _SnapshotReader:
    """Request-local immutable view; never stores tenant state on the shared retriever."""

    def __init__(self, tenant_id: str, documents: tuple[StoredDocumentVersion, ...]):
        self.tenant_id, self.documents = tenant_id, documents
        self.byte_count = sum(len(doc.model_dump_json().encode("utf-8")) for doc in documents)

    def read_latest_documents(self, *, tenant_id, max_documents, max_snapshot_bytes):
        if tenant_id != self.tenant_id:
            raise RetrievalResultError("Hybrid child requested another tenant")
        if len(self.documents) > max_documents or self.byte_count > max_snapshot_bytes:
            raise RetrievalCapacityExceeded("Hybrid snapshot exceeds child admission")
        return self.documents


class HybridRetriever:
    def __init__(
        self,
        *,
        reader: DocumentCorpusReaderInterface,
        children_factory: Callable[
            [DocumentCorpusReaderInterface], tuple[PassageRetrieverInterface, ...]
        ],
        fusion: RankedResultFusionInterface,
        candidate_k: int = 20,
        max_documents: int = 20,
        max_snapshot_bytes: int = 8_000_000,
        max_chunks: int = 512,
        max_output_chars: int = 100_000,
    ):
        if type(candidate_k) is not int or not 1 <= candidate_k <= 20:
            raise ValueError("Hybrid candidate_k must be an integer between 1 and 20")
        if any(
            type(value) is not int or value < 1
            for value in (max_documents, max_snapshot_bytes, max_chunks, max_output_chars)
        ):
            raise ValueError("Hybrid admission limits must be positive integers")
        self._reader, self._children_factory, self._fusion = reader, children_factory, fusion
        self._candidate_k = candidate_k
        self._max_documents, self._max_bytes = max_documents, max_snapshot_bytes
        self._max_chunks, self._max_output = max_chunks, max_output_chars

    def _snapshot(self, tenant_id):
        guard_embedding_work()
        raw = self._reader.read_latest_documents(
            tenant_id=tenant_id,
            max_documents=self._max_documents,
            max_snapshot_bytes=self._max_bytes,
        )
        documents = tuple(
            StoredDocumentVersion.model_validate_json(doc.model_dump_json()) for doc in raw
        )
        if any(doc.tenant_id != tenant_id for doc in documents):
            raise RetrievalResultError("Hybrid corpus contains another tenant")
        if len({doc.document_id for doc in documents}) != len(documents):
            raise RetrievalResultError("Hybrid corpus contains duplicate document identities")
        if (
            len(documents) > self._max_documents
            or sum(len(doc.model_dump_json().encode("utf-8")) for doc in documents)
            > self._max_bytes
            or sum(len(doc.chunking.chunks) for doc in documents) > self._max_chunks
        ):
            raise RetrievalCapacityExceeded("Hybrid corpus exceeds admission")
        return documents

    @staticmethod
    def _validate_result(result, *, limit, evidence):
        if not isinstance(result, RetrievalResult):
            raise RetrievalResultError("Hybrid component returned an invalid result type")
        result = RetrievalResult.model_validate_json(result.model_dump_json())
        if len(result.passages) > limit:
            raise RetrievalResultError("Hybrid component exceeded passage limit")
        for passage in result.passages:
            key = (passage.document_id, passage.document_version, passage.chunk.index)
            if evidence.get(key) != passage:
                raise RetrievalResultError(
                    "Hybrid component returned evidence outside the source snapshot"
                )
        return result

    def search(self, *, request: RetrievalRequest, tenant_id: str) -> RetrievalResult:
        tenant_id = ToolExecutionContext(tenant_id=tenant_id).tenant_id
        request = RetrievalRequest.model_validate_json(request.model_dump_json())
        if request.top_k > self._candidate_k:
            raise RetrievalCapacityExceeded("Requested top_k exceeds hybrid candidate_k")
        documents = self._snapshot(tenant_id)
        if not documents:
            return RetrievalResult(strategy="hybrid_fusion_v1")
        evidence = {
            (doc.document_id, doc.version, chunk.index): RetrievedPassage(
                document_id=doc.document_id,
                document_version=doc.version,
                title=doc.document.title,
                source_ref=doc.document.source_ref,
                chunk=chunk,
            )
            for doc in documents
            for chunk in doc.chunking.chunks
        }
        children = self._children_factory(_SnapshotReader(tenant_id, documents))
        if not 2 <= len(children) <= 8:
            raise RetrievalResultError("Hybrid requires two to eight child retrievers")
        child_request = request.model_copy(update={"top_k": self._candidate_k})
        results = []
        for child in children:
            guard_embedding_work()
            results.append(
                self._validate_result(
                    child.search(request=child_request, tenant_id=tenant_id),
                    limit=self._candidate_k,
                    evidence=evidence,
                )
            )
        fused = self._validate_result(
            self._fusion.fuse(
                request=RankedResultFusionRequest(request=request, ranked_results=tuple(results))
            ),
            limit=request.top_k,
            evidence=evidence,
        )
        candidates = {passage for result in results for passage in result.passages}
        if any(passage not in candidates for passage in fused.passages):
            raise RetrievalResultError("Fusion introduced a passage absent from child results")
        if sum(len(p.chunk.text) for p in fused.passages) > self._max_output:
            raise RetrievalCapacityExceeded("Hybrid output exceeds character admission")
        current = self._snapshot(tenant_id)
        if {d.document_id: d for d in current} != {d.document_id: d for d in documents}:
            raise RetrievalResultError(
                "Source corpus changed during hybrid search; retry explicitly"
            )
        guard_embedding_work()
        logger.info(
            {
                "event": "hybrid_search_fused",
                "tenant_id": tenant_id,
                "child_count": len(results),
                "candidate_count": len(candidates),
                "passage_count": len(fused.passages),
                "fusion_strategy": fused.strategy,
            }
        )
        return RetrievalResult(strategy="hybrid_fusion_v1", passages=fused.passages)
