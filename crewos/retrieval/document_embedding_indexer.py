"""Bounded read-through embedding cache for authorized current source snapshots."""

import logging

from pydantic import ValidationError

from crewos.application.services.execution_control import (
    get_execution_control,
    ResourceBudgetExceeded,
)
from crewos.ingestion import StoredDocumentVersion
from crewos.retrieval.chunk_embedding_store import (
    ChunkEmbeddingStoreInterface,
    EmbeddingStorageCorruption,
)
from crewos.retrieval.embedding_contracts import EmbeddingBatch, TextEmbeddingRequest
from crewos.retrieval.embedding_storage_contracts import (
    ChunkEmbeddingWriteBatch,
    EmbeddingSpace,
    StoredChunkEmbedding,
)
from crewos.retrieval.passage_retriever import RetrievalCapacityExceeded, RetrievalResultError
from crewos.retrieval.search_contracts import RetrievedPassage
from crewos.retrieval.text_embedding import TextEmbeddingInterface

logger = logging.getLogger(__name__)


class EmbeddingWorkBudget:
    def __init__(self, max_input_bytes: int = 262144):
        if type(max_input_bytes) is not int or max_input_bytes < 1:
            raise ValueError("Embedding input byte limit must be positive")
        self.remaining = max_input_bytes

    def reserve(self, amount: int) -> None:
        if amount > self.remaining:
            raise RetrievalCapacityExceeded("Embedding input exceeds per-search byte budget")
        self.remaining -= amount


def guard_embedding_work() -> None:
    control = get_execution_control()
    if control is not None:
        control.tool_remaining()


class DocumentEmbeddingIndexer:
    def __init__(
        self,
        *,
        provider: TextEmbeddingInterface,
        store: ChunkEmbeddingStoreInterface,
        space: EmbeddingSpace,
    ):
        self.space = EmbeddingSpace.model_validate_json(space.model_dump_json())
        if (
            self.space.preprocessing_revision != "identity_v1"
            or self.space.normalization != "provider"
        ):
            raise ValueError(
                "Only identity_v1 preprocessing and provider normalization are implemented"
            )
        self._provider, self._store = provider, store

    def embed_texts(
        self, texts: tuple[str, ...], *, prefix: str, budget: EmbeddingWorkBudget
    ) -> EmbeddingBatch:
        try:
            request = TextEmbeddingRequest(texts=tuple(prefix + text for text in texts))
        except ValidationError:
            raise RetrievalCapacityExceeded("Prepared embedding inputs exceed admission") from None
        amount = sum(len(text.encode("utf-8")) for text in request.texts)
        budget.reserve(amount)
        control = get_execution_control()
        if control is not None:
            control.tool_remaining()
            # Chat price rates cannot price a separate embedding model correctly.
            if control.input_rate is not None or control.max_cost is not None:
                raise ResourceBudgetExceeded(
                    "Priced runs require separate embedding cost accounting"
                )
            # Conservative UTF-8 byte reservation, not a tokenizer usage measurement.
            control.reserve(amount, 0)
        raw = self._provider.embed(request=request)
        if not isinstance(raw, EmbeddingBatch):
            raise RetrievalResultError("Embedding provider returned an invalid result type")
        batch = EmbeddingBatch.model_validate_json(raw.model_dump_json()).validate_for(request)
        if (
            batch.model_id != self.space.model_id
            or batch.model_revision != self.space.model_revision
            or batch.dimensions != self.space.dimensions
        ):
            raise RetrievalResultError("Embedding result differs from configured space")
        guard_embedding_work()
        return batch

    def ensure_document(
        self,
        document: StoredDocumentVersion,
        *,
        budget: EmbeddingWorkBudget,
        max_records: int,
        max_snapshot_bytes: int,
    ) -> tuple[StoredChunkEmbedding, ...]:
        document = StoredDocumentVersion.model_validate_json(document.model_dump_json())
        if type(max_records) is not int or max_records < len(document.chunking.chunks):
            raise RetrievalCapacityExceeded("Document exceeds embedding record admission")
        guard_embedding_work()
        existing = self._store.read_document(
            tenant_id=document.tenant_id,
            document_id=document.document_id,
            version=document.version,
            space=self.space,
            max_records=max_records,
            max_snapshot_bytes=max_snapshot_bytes,
        )
        passages = tuple(
            RetrievedPassage(
                document_id=document.document_id,
                document_version=document.version,
                title=document.document.title,
                source_ref=document.document.source_ref,
                chunk=chunk,
            )
            for chunk in document.chunking.chunks
        )
        by_index = {}
        for raw in existing:
            record = StoredChunkEmbedding.model_validate_json(raw.model_dump_json())
            index = record.passage.chunk.index
            if (
                record.tenant_id != document.tenant_id
                or record.space != self.space
                or index >= len(passages)
                or record.passage != passages[index]
                or index in by_index
            ):
                raise EmbeddingStorageCorruption("Cached embedding does not match current source")
            by_index[index] = record
        missing = [passage for passage in passages if passage.chunk.index not in by_index]
        # Plan all batches before the first provider call; never truncate oversized chunks.
        groups = []
        current, characters = [], 0
        for passage in missing:
            prepared = self.space.document_prefix + passage.chunk.text
            try:
                TextEmbeddingRequest(texts=(prepared,))
            except ValidationError:
                raise RetrievalCapacityExceeded(
                    "Prepared chunk exceeds embedding input admission"
                ) from None
            if current and (len(current) == 128 or characters + len(prepared) > 65536):
                groups.append(tuple(current))
                current, characters = [], 0
            current.append(passage)
            characters += len(prepared)
        if current:
            groups.append(tuple(current))
        needed = sum(
            len((self.space.document_prefix + p.chunk.text).encode("utf-8")) for p in missing
        )
        if needed > budget.remaining:
            raise RetrievalCapacityExceeded(
                "Document embeddings exceed remaining input byte budget"
            )
        for group in groups:
            batch = self.embed_texts(
                tuple(p.chunk.text for p in group), prefix=self.space.document_prefix, budget=budget
            )
            records = tuple(
                StoredChunkEmbedding(
                    tenant_id=document.tenant_id, space=self.space, passage=passage, vector=vector
                )
                for passage, vector in zip(group, batch.vectors)
            )
            guard_embedding_work()
            self._store.put(ChunkEmbeddingWriteBatch(records=records))
            for record in records:
                by_index[record.passage.chunk.index] = record
        logger.info(
            {
                "event": "document_embeddings_ready",
                "tenant_id": document.tenant_id,
                "chunk_count": len(passages),
                "cache_hits": len(existing),
                "generated_count": len(missing),
            }
        )
        return tuple(by_index[index] for index in range(len(passages)))
