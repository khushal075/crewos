"""Derived embedding identity, configuration separation and batch admission."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from crewos.chunking import TextChunk
from crewos.retrieval.embedding_storage_contracts import (
    ChunkEmbeddingWriteBatch,
    EmbeddingSpace,
    StoredChunkEmbedding,
)
from crewos.retrieval.search_contracts import RetrievedPassage


def space(**changes):
    return EmbeddingSpace.model_validate(
        {
            "provider_id": "ollama_native_v1",
            "model_id": "fixture-model",
            "model_revision": "fixture-digest",
            "dimensions": 2,
            "preprocessing_revision": "identity_v1",
            **changes,
        }
    )


def record(**changes):
    return StoredChunkEmbedding.model_validate(
        {
            "tenant_id": "tenant_a",
            "space": space(),
            "vector": (1.0, 0.0),
            "passage": RetrievedPassage(
                document_id=uuid4(),
                document_version=1,
                title="Runbook",
                source_ref="runbook:integration",
                chunk=TextChunk(index=0, text="Expired token.", start_char=0, end_char=14),
            ),
            **changes,
        }
    )


def test_round_trip_preserves_scope_source_version_text_and_positions():
    first = record()
    write = ChunkEmbeddingWriteBatch(records=(first,))
    restored = ChunkEmbeddingWriteBatch.model_validate_json(write.model_dump_json())
    assert restored == write
    assert restored.records[0].passage.chunk.text == "Expired token."
    assert restored.records[0].passage.document_version == 1
    assert restored.records[0].tenant_id == "tenant_a"


def test_space_key_is_stable_across_serialization_and_field_order():
    original = space()
    assert original.key == EmbeddingSpace.model_validate_json(original.model_dump_json()).key
    assert (
        original.key
        == EmbeddingSpace.model_validate(dict(reversed(list(original.model_dump().items())))).key
    )
    assert len(original.key) == 64


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_id": "another_provider"},
        {"model_id": "another-model"},
        {"model_revision": "revision-2"},
        {"dimensions": 3},
        {"preprocessing_revision": "normalization_v2"},
        {"document_prefix": "search_document: "},
        {"query_prefix": "search_query: "},
        {"normalization": "l2"},
    ],
)
def test_each_representation_setting_changes_the_storage_namespace(changes):
    assert space(**changes).key != space().key


@pytest.mark.parametrize(
    "changes",
    [
        {"model_revision": " "},
        {"provider_id": ""},
        {"dimensions": True},
        {"dimensions": 0},
        {"dimensions": 16385},
        {"preprocessing_revision": ""},
        {"document_prefix": "x" * 1025},
        {"query_prefix": 123},
        {"normalization": "unknown"},
        {"permissions": ["admin"]},
    ],
)
def test_rejects_invalid_configuration(changes):
    with pytest.raises(ValidationError):
        space(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"tenant_id": "tenant/a"},
        {"tenant_id": ""},
        {"vector": ()},
        {"vector": (1.0,)},
        {"vector": (0.0, 0.0)},
        {"vector": (True, 0.0)},
        {"vector": ("1", 0.0)},
        {"vector": (float("nan"), 0.0)},
        {"vector": (float("inf"), 0.0)},
        {"permissions": ["admin"]},
    ],
)
def test_rejects_invalid_persisted_record_shapes(changes):
    with pytest.raises(ValidationError):
        record(**changes)


def test_rejects_cross_tenant_write_batch():
    with pytest.raises(ValidationError, match="exactly one tenant"):
        ChunkEmbeddingWriteBatch(records=(record(), record(tenant_id="tenant_b")))


def test_rejects_duplicate_source_configuration_keys_even_with_different_vectors():
    first = record()
    second = record(passage=first.passage, vector=(0.0, 1.0))
    with pytest.raises(ValidationError, match="distinct source"):
        ChunkEmbeddingWriteBatch(records=(first, second))


def test_distinct_versions_and_configurations_can_coexist():
    first = record()
    new_version = first.passage.model_dump()
    new_version["document_version"] = 2
    second = record(passage=new_version)
    third = record(passage=first.passage, space=space(model_revision="revision-2"))
    assert len(ChunkEmbeddingWriteBatch(records=(first, second, third)).records) == 3


@pytest.mark.parametrize("count", [0, 129])
def test_write_batch_count_is_bounded(count):
    with pytest.raises(ValidationError):
        ChunkEmbeddingWriteBatch(records=tuple(record() for _ in range(count)))


def test_write_batch_text_admission():
    records = []
    for _ in range(2):
        passage = record().passage.model_dump()
        passage["chunk"] = {"index": 0, "text": "x" * 50001, "start_char": 0, "end_char": 50001}
        records.append(record(passage=passage))
    with pytest.raises(ValidationError, match="100000"):
        ChunkEmbeddingWriteBatch(records=records)


def test_records_and_configuration_are_immutable():
    first = record()
    with pytest.raises(ValidationError):
        first.tenant_id = "tenant_b"
    with pytest.raises(ValidationError):
        first.space.model_revision = "changed"
    with pytest.raises(TypeError):
        first.vector[0] = 3.0
