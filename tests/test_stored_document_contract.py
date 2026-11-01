"""Immutable tenant-scoped snapshots preserve source and chunk provenance."""

from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crewos.chunking import ChunkingOptions, ChunkingRequest, build_character_chunking
from crewos.ingestion import DocumentInput, StoredDocumentVersion


def payload():
    document = DocumentInput(
        title="CRM runbook", text="Check logs before retrying.", source_ref="runbook:v1"
    )
    return {
        "document_id": uuid4(),
        "tenant_id": "tenant_a",
        "version": 1,
        "document": document,
        "chunking": build_character_chunking().chunk(ChunkingRequest(text=document.text)),
    }


def test_snapshot_round_trips_with_identity_and_chunking_provenance():
    record = StoredDocumentVersion(**payload())
    restored = StoredDocumentVersion.model_validate_json(record.model_dump_json())
    assert restored == record
    assert record.created_at.utcoffset().total_seconds() == 0
    assert record.chunking.strategy == "character_window_v1"
    assert record.document.source_ref == "runbook:v1"
    assert record.chunking.chunks[0].text == record.document.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", ""),
        ("tenant_id", "tenant/a"),
        ("tenant_id", 123),
        ("document_id", "not-a-uuid"),
        ("version", 0),
        ("version", True),
        ("version", "1"),
        ("created_at", datetime(2026, 11, 1)),
        ("permissions", ["write"]),
    ],
)
def test_rejects_invalid_snapshot_fields(field, value):
    with pytest.raises(ValidationError):
        StoredDocumentVersion.model_validate({**payload(), field: value})


@pytest.mark.parametrize(
    "chunks",
    [
        [],
        [{"index": 0, "text": "Wrong", "start_char": 0, "end_char": 5}],
        [{"index": 0, "text": "Check", "start_char": 0, "end_char": 5}],
        [{"index": 1, "text": "Check logs before retrying.", "start_char": 0, "end_char": 27}],
        [{"index": 0, "text": "logs", "start_char": 6, "end_char": 10}],
    ],
)
def test_rejects_empty_wrong_or_incomplete_chunk_sets(chunks):
    data = StoredDocumentVersion(**payload()).model_dump(mode="json")
    data["chunking"]["chunks"] = chunks
    with pytest.raises(ValidationError):
        StoredDocumentVersion.model_validate(data)


def test_snapshot_allows_valid_overlapping_chunks():
    data = payload()
    data["chunking"] = build_character_chunking().chunk(
        ChunkingRequest(
            text=data["document"].text,
            options=ChunkingOptions(chunk_size=10, overlap=2),
        )
    )
    record = StoredDocumentVersion(**data)
    assert len(record.chunking.chunks) == 4


def test_snapshot_and_nested_data_are_immutable():
    record = StoredDocumentVersion(**payload())
    with pytest.raises(ValidationError):
        record.version = 2
    with pytest.raises(ValidationError):
        record.document.text = "Changed"
    with pytest.raises(ValidationError):
        record.chunking.chunks[0].text = "Changed"
