"""Bounded exact cosine search over current tenant snapshots and persistent vectors."""

import logging
from math import isfinite

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.ingestion import DocumentCorpusReaderInterface, StoredDocumentVersion
from crewos.retrieval.document_embedding_indexer import (
    DocumentEmbeddingIndexer,
    EmbeddingWorkBudget,
    guard_embedding_work,
)
from crewos.retrieval.passage_retriever import RetrievalCapacityExceeded, RetrievalResultError
from crewos.retrieval.search_contracts import RetrievalRequest, RetrievalResult
from crewos.retrieval.strategies.cosine_similarity import CosineSimilarity
from crewos.retrieval.vector_similarity import VectorSimilarityInterface

logger = logging.getLogger(__name__)


class VectorRetriever:
    def __init__(
        self,
        reader: DocumentCorpusReaderInterface,
        *,
        indexer: DocumentEmbeddingIndexer,
        similarity: VectorSimilarityInterface | None = None,
        max_documents: int = 20,
        max_snapshot_bytes: int = 8_000_000,
        max_chunks: int = 512,
        max_embedding_bytes: int = 32_000_000,
        max_embedding_input_bytes: int = 262144,
        max_output_chars: int = 100_000,
    ):
        if any(
            type(value) is not int or value < 1
            for value in (
                max_documents,
                max_snapshot_bytes,
                max_chunks,
                max_embedding_bytes,
                max_embedding_input_bytes,
                max_output_chars,
            )
        ):
            raise ValueError("Vector search admission limits must be positive integers")
        self._reader, self._indexer = reader, indexer
        self._similarity = similarity if similarity is not None else CosineSimilarity()
        self._max_documents, self._max_source_bytes = max_documents, max_snapshot_bytes
        self._max_chunks, self._max_embedding_bytes = max_chunks, max_embedding_bytes
        self._max_input_bytes, self._max_output = max_embedding_input_bytes, max_output_chars

    def _read_current(self, tenant_id: str) -> tuple[StoredDocumentVersion, ...]:
        guard_embedding_work()
        raw_documents = self._reader.read_latest_documents(
            tenant_id=tenant_id,
            max_documents=self._max_documents,
            max_snapshot_bytes=self._max_source_bytes,
        )
        documents = tuple(
            StoredDocumentVersion.model_validate_json(doc.model_dump_json())
            for doc in raw_documents
        )
        if any(doc.tenant_id != tenant_id for doc in documents):
            raise RetrievalResultError("Corpus reader returned another tenant's document")
        if len({doc.document_id for doc in documents}) != len(documents):
            raise RetrievalResultError("Corpus reader returned duplicate document identities")
        if (
            len(documents) > self._max_documents
            or sum(len(doc.model_dump_json().encode("utf-8")) for doc in documents)
            > self._max_source_bytes
            or sum(len(doc.chunking.chunks) for doc in documents) > self._max_chunks
        ):
            raise RetrievalCapacityExceeded("Current vector corpus exceeds admission")
        return documents

    def search(self, *, request: RetrievalRequest, tenant_id: str) -> RetrievalResult:
        tenant_id = ToolExecutionContext(tenant_id=tenant_id).tenant_id
        request = RetrievalRequest.model_validate_json(request.model_dump_json())
        documents = self._read_current(tenant_id)
        if not documents:
            return RetrievalResult(strategy="vector_cosine_v1")
        # Plan all prepared text before any provider call, including query prefixes.
        from crewos.retrieval.embedding_contracts import TextEmbeddingRequest
        from pydantic import ValidationError

        prepared = [self._indexer.space.query_prefix + request.query]
        prepared.extend(
            self._indexer.space.document_prefix + chunk.text
            for doc in documents
            for chunk in doc.chunking.chunks
        )
        try:
            for text in prepared:
                TextEmbeddingRequest(texts=(text,))
        except ValidationError:
            raise RetrievalCapacityExceeded(
                "Prepared vector search text exceeds input admission"
            ) from None
        if sum(len(text.encode("utf-8")) for text in prepared) > self._max_input_bytes:
            raise RetrievalCapacityExceeded("Vector corpus exceeds embedding input byte admission")
        budget = EmbeddingWorkBudget(self._max_input_bytes)
        query = self._indexer.embed_texts(
            (request.query,), prefix=self._indexer.space.query_prefix, budget=budget
        )
        candidates = []
        remaining_bytes = self._max_embedding_bytes
        for document in documents:
            guard_embedding_work()
            if remaining_bytes < 1:
                raise RetrievalCapacityExceeded("Vector corpus exceeds embedding byte admission")
            records = self._indexer.ensure_document(
                document,
                budget=budget,
                max_records=self._max_chunks,
                max_snapshot_bytes=remaining_bytes,
            )
            remaining_bytes -= sum(
                len(record.model_dump_json().encode("utf-8")) for record in records
            )
            if remaining_bytes < 0:
                raise RetrievalCapacityExceeded("Vector corpus exceeds embedding byte admission")
            for record in records:
                score = self._similarity.score(left=query.vectors[0], right=record.vector)
                if type(score) not in (int, float) or not isfinite(score):
                    raise RetrievalResultError("Similarity returned an invalid score")
                candidates.append((score, record.passage))
        candidates.sort(
            key=lambda item: (
                -item[0],
                str(item[1].document_id),
                item[1].document_version,
                item[1].chunk.index,
            )
        )
        selected = tuple(item[1] for item in candidates[: request.top_k])
        if sum(len(passage.chunk.text) for passage in selected) > self._max_output:
            raise RetrievalCapacityExceeded("Selected passages exceed output character limit")
        # Fail closed if source lifecycle changed while inference/cache work ran.
        current = self._read_current(tenant_id)
        if {doc.document_id: doc for doc in current} != {doc.document_id: doc for doc in documents}:
            raise RetrievalResultError(
                "Source corpus changed during vector search; retry explicitly"
            )
        guard_embedding_work()
        logger.info(
            {
                "event": "vector_search_ranked",
                "tenant_id": tenant_id,
                "candidate_count": len(candidates),
                "passage_count": len(selected),
            }
        )
        return RetrievalResult(strategy="vector_cosine_v1", passages=selected)
