"""Current-source embedding preparation, batching, cache reuse and failure checks."""

from unittest.mock import Mock

import pytest

from crewos.application.services.execution_control import (
    ExecutionControl,
    ResourceBudgetExceeded,
    execution_scope,
)
from crewos.chunking import ChunkingOptions, ChunkingRequest, build_character_chunking
from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
from crewos.retrieval.chunk_embedding_store import EmbeddingStorageCorruption
from crewos.retrieval.document_embedding_indexer import (
    DocumentEmbeddingIndexer,
    EmbeddingWorkBudget,
)
from crewos.retrieval.embedding_contracts import EmbeddingBatch
from crewos.retrieval.embedding_storage_contracts import (
    ChunkEmbeddingWriteBatch,
    StoredChunkEmbedding,
)
from crewos.retrieval.passage_retriever import RetrievalCapacityExceeded, RetrievalResultError
from tests.test_embedding_storage_contracts import space
from tests.test_keyword_retrieval import save
from crewos.ingestion import build_sqlite_document_store


class ExampleEmbeddingProvider:
    def __init__(self, configured_space=None):
        self.space = configured_space or space()
        self.requests = []

    def embed(self, *, request):
        self.requests.append(request)
        return EmbeddingBatch(
            model_id=self.space.model_id,
            model_revision=self.space.model_revision,
            dimensions=self.space.dimensions,
            vectors=tuple((1.0, 0.0) for _ in request.texts),
        )


@pytest.fixture
def setup(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.sqlite3")
    cache = SQLiteChunkEmbeddingStore(tmp_path / "cache.sqlite3")
    provider = ExampleEmbeddingProvider()
    indexer = DocumentEmbeddingIndexer(provider=provider, store=cache, space=space())
    return source, cache, provider, indexer


def ensure(indexer, document, **changes):
    return indexer.ensure_document(
        document,
        **{
            "budget": EmbeddingWorkBudget(),
            "max_records": 512,
            "max_snapshot_bytes": 32_000_000,
            **changes,
        },
    )


def test_generates_persists_and_reuses_exact_current_passages(setup):
    source, cache, provider, indexer = setup
    document = save(source, "Expired token.")
    first = ensure(indexer, document)
    assert first[0].passage.chunk == document.chunking.chunks[0]
    assert first[0].tenant_id == document.tenant_id
    assert ensure(indexer, document) == first
    assert len(provider.requests) == 1


def test_only_missing_chunks_are_embedded_after_partial_progress(setup):
    source, cache, provider, indexer = setup
    document = save(source, "a" * 150)
    records = ensure(indexer, document)
    cache.delete_document(tenant_id=document.tenant_id, document_id=document.document_id)
    cache.put(ChunkEmbeddingWriteBatch(records=(records[0],)))
    provider.requests.clear()
    assert ensure(indexer, document) == records
    assert provider.requests[0].texts == (document.chunking.chunks[1].text,)


def test_batches_more_than_128_chunks_without_losing_order(setup):
    source, _, provider, indexer = setup
    document = save(source, "a" * 129)
    document = document.model_copy(
        update={
            "chunking": build_character_chunking().chunk(
                ChunkingRequest(
                    text=document.document.text, options=ChunkingOptions(chunk_size=1, overlap=0)
                )
            )
        }
    )
    records = ensure(indexer, document)
    assert [len(request.texts) for request in provider.requests] == [128, 1]
    assert [record.passage.chunk.index for record in records] == list(range(129))


def test_prefix_preserves_original_source_text(setup):
    source, cache, _, _ = setup
    configured = space(document_prefix="search_document: ", query_prefix="search_query: ")
    provider = ExampleEmbeddingProvider(configured)
    indexer = DocumentEmbeddingIndexer(provider=provider, store=cache, space=configured)
    document = save(source, "Expired token.")
    records = ensure(indexer, document)
    assert provider.requests[0].texts == ("search_document: Expired token.",)
    assert records[0].passage.chunk.text == "Expired token."


def test_input_byte_budget_fails_before_provider_call(setup):
    source, _, provider, indexer = setup
    with pytest.raises(RetrievalCapacityExceeded):
        ensure(indexer, save(source, "private content"), budget=EmbeddingWorkBudget(1))
    assert provider.requests == []


def test_corrupt_or_wrongly_scoped_cache_fails_before_provider(setup):
    source, _, provider, _ = setup
    document = save(source, "Expired token.")
    from tests.test_embedding_storage_contracts import record

    store = Mock()
    store.read_document.return_value = (record(tenant_id="tenant_b"),)
    indexer = DocumentEmbeddingIndexer(provider=provider, store=store, space=space())
    with pytest.raises(EmbeddingStorageCorruption):
        ensure(indexer, document)
    assert provider.requests == []


def test_wrong_model_result_is_not_saved(setup):
    source, cache, _, _ = setup
    provider = ExampleEmbeddingProvider(space(model_revision="other"))
    indexer = DocumentEmbeddingIndexer(provider=provider, store=cache, space=space())
    with pytest.raises(RetrievalResultError, match="configured space"):
        ensure(indexer, save(source, "Expired token."))


def test_provider_failures_propagate_without_empty_success(setup):
    source, cache, _, _ = setup
    provider = Mock()
    provider.embed.side_effect = OSError("offline")
    indexer = DocumentEmbeddingIndexer(provider=provider, store=cache, space=space())
    with pytest.raises(OSError):
        ensure(indexer, save(source, "Expired token."))


def test_rejects_invalid_provider_type(setup):
    source, cache, _, _ = setup
    provider = Mock()
    provider.embed.return_value = []
    indexer = DocumentEmbeddingIndexer(provider=provider, store=cache, space=space())
    with pytest.raises(RetrievalResultError):
        ensure(indexer, save(source, "Expired token."))


def test_token_budget_and_priced_run_rejection_precede_model_call(setup):
    source, _, provider, indexer = setup
    document = save(source, "Expired token.")
    with execution_scope(ExecutionControl(max_tokens=1)):
        with pytest.raises(ResourceBudgetExceeded):
            ensure(indexer, document)
    with execution_scope(ExecutionControl(input_usd_per_million=1, output_usd_per_million=1)):
        with pytest.raises(ResourceBudgetExceeded, match="separate embedding"):
            ensure(indexer, document)
    assert provider.requests == []


def test_unimplemented_configuration_is_rejected(setup):
    _, cache, provider, _ = setup
    with pytest.raises(ValueError):
        DocumentEmbeddingIndexer(provider=provider, store=cache, space=space(normalization="l2"))


def test_oversized_prepared_chunk_fails_before_model_call(setup):
    source, _, provider, indexer = setup
    document = save(source, "x" * 9000)
    document = document.model_copy(
        update={
            "chunking": build_character_chunking().chunk(
                ChunkingRequest(
                    text=document.document.text, options=ChunkingOptions(chunk_size=9000, overlap=0)
                )
            )
        }
    )
    with pytest.raises(RetrievalCapacityExceeded, match="Prepared chunk"):
        ensure(indexer, document)
    assert provider.requests == []


def test_batches_at_aggregate_character_limit(setup):
    source, _, provider, indexer = setup
    document = save(source, "a" * 80000)
    document = document.model_copy(
        update={
            "chunking": build_character_chunking().chunk(
                ChunkingRequest(
                    text=document.document.text, options=ChunkingOptions(chunk_size=4000, overlap=0)
                )
            )
        }
    )
    assert len(ensure(indexer, document)) == 20
    assert [len(request.texts) for request in provider.requests] == [16, 4]


def test_successful_embedding_reserves_conservative_input_tokens(setup):
    source, _, _, indexer = setup
    document = save(source, "Expired token.")
    control = ExecutionControl()
    with execution_scope(control):
        ensure(indexer, document)
    assert control.state["reserved_tokens"] == len(document.document.text.encode("utf-8"))


def test_embedding_progress_logs_exclude_source_and_vectors(setup, caplog):
    import logging

    source, _, _, indexer = setup
    document = save(source, "private_source_marker")
    with caplog.at_level(logging.INFO):
        ensure(indexer, document)
    assert "embedding_batch_started" in caplog.text
    assert "embedding_batch_succeeded" in caplog.text
    assert "private_source_marker" not in caplog.text
    assert "vectors" not in caplog.text
