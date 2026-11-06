"""FTS-backed keyword retrieval without loading/scanning the whole tenant corpus."""

import logging
import re
from crewos.ingestion import StoredDocumentVersion
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.retrieval.document_embedding_indexer import guard_embedding_work
from crewos.retrieval.keyword_search_index import KeywordSearchIndexInterface
from crewos.retrieval.passage_retriever import RetrievalCapacityExceeded, RetrievalResultError
from crewos.retrieval.search_contracts import RetrievalRequest, RetrievalResult, RetrievedPassage

logger = logging.getLogger(__name__)


class IndexedBM25Retriever:
    def __init__(
        self,
        index: KeywordSearchIndexInterface,
        *,
        max_snapshot_bytes=8_000_000,
        max_output_chars=100_000,
    ):
        if any(type(v) is not int or v < 1 for v in (max_snapshot_bytes, max_output_chars)):
            raise ValueError("Indexed retrieval limits must be positive integers")
        self._index, self._max_bytes, self._max_output = index, max_snapshot_bytes, max_output_chars

    def search(self, *, request: RetrievalRequest, tenant_id: str) -> RetrievalResult:
        tenant_id = ToolExecutionContext(tenant_id=tenant_id).tenant_id
        request = RetrievalRequest.model_validate_json(request.model_dump_json())
        terms = tuple(sorted(set(re.findall(r"\w+", request.query.casefold()))))
        if len(terms) > 64:
            raise RetrievalCapacityExceeded("Indexed query exceeds term admission")
        guard_embedding_work()
        rows = self._index.search_current_chunks(
            tenant_id=tenant_id,
            terms=terms,
            limit=request.top_k,
            max_snapshot_bytes=self._max_bytes,
        )
        passages = []
        for doc, index in rows:
            doc = StoredDocumentVersion.model_validate_json(doc.model_dump_json())
            if (
                doc.tenant_id != tenant_id
                or type(index) is not int
                or not 0 <= index < len(doc.chunking.chunks)
            ):
                raise RetrievalResultError("Index returned invalid tenant/chunk scope")
            passages.append(
                RetrievedPassage(
                    document_id=doc.document_id,
                    document_version=doc.version,
                    title=doc.document.title,
                    source_ref=doc.document.source_ref,
                    chunk=doc.chunking.chunks[index],
                )
            )
        if len(passages) > request.top_k:
            raise RetrievalResultError("Index exceeded requested limit")
        if sum(len(p.chunk.text) for p in passages) > self._max_output:
            raise RetrievalCapacityExceeded("Indexed output exceeds character admission")
        guard_embedding_work()
        logger.info(
            {
                "event": "indexed_search_ranked",
                "tenant_id": tenant_id,
                "passage_count": len(passages),
            }
        )
        return RetrievalResult(strategy="sqlite_fts5_bm25_v1", passages=tuple(passages))
