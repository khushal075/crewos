"""Internal processing owns sequencing and snapshot assembly, not authorization."""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from crewos.chunking import ChunkingOptions, ChunkingRequest, build_character_chunking
from crewos.ingestion import DocumentIngestionRequest, build_plain_text_preparation
from crewos.ingestion.document_processing_coordinator import DocumentProcessingCoordinator


def test_coordinates_data_flow_and_returns_the_stored_snapshot():
    source = DocumentIngestionRequest(content=b"Check logs. Retry later.", title="Incident")
    options = ChunkingOptions(chunk_size=10, overlap=2)
    prepared = build_plain_text_preparation().prepare(content=source.content, title=source.title)
    chunks = build_character_chunking().chunk(ChunkingRequest(text=prepared.text, options=options))
    preparation, chunking, store = Mock(), Mock(), Mock()
    preparation.prepare.return_value = prepared
    chunking.chunk.return_value = chunks
    store.create_version.side_effect = lambda record: record
    coordinator = DocumentProcessingCoordinator(
        preparation=preparation, chunking=chunking, store=store, chunking_options=options
    )
    saved = coordinator.process(request=source, tenant_id="tenant_a")
    assert saved.document == prepared
    assert saved.chunking == chunks
    assert saved.tenant_id == "tenant_a"
    assert saved.version == 1
    assert saved.document_id.version == 4
    preparation.prepare.assert_called_once_with(
        content=source.content, title=source.title, source_ref=None
    )
    chunking.chunk.assert_called_once_with(ChunkingRequest(text=prepared.text, options=options))
    store.create_version.assert_called_once_with(saved)


def test_inconsistent_component_output_never_reaches_storage():
    prepared = build_plain_text_preparation().prepare(content=b"Correct source", title="Incident")
    preparation, chunking, store = Mock(), Mock(), Mock()
    preparation.prepare.return_value = prepared
    chunking.chunk.return_value = build_character_chunking().chunk(
        ChunkingRequest(text="Wrong source")
    )
    coordinator = DocumentProcessingCoordinator(
        preparation=preparation, chunking=chunking, store=store
    )
    with pytest.raises(ValidationError):
        coordinator.process(
            request=DocumentIngestionRequest(content=b"Correct source", title="Incident"),
            tenant_id="tenant_a",
        )
    store.create_version.assert_not_called()
