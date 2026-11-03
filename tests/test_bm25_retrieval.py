from unittest.mock import Mock
from uuid import UUID

import pytest

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyDeniedError, PolicyEnforcedExecutor
from crewos.chunking import ChunkingOptions, ChunkingRequest, build_character_chunking
from crewos.ingestion import DocumentInput, StoredDocumentVersion, build_sqlite_document_store
from crewos.retrieval import (
    RetrievalCapacityExceeded,
    RetrievalRequest,
    RetrievalResultError,
    build_bm25_retrieval,
)
from crewos.retrieval.strategies.bm25 import BM25Retriever


def record(number, text, *, tenant="a", version=1):
    return StoredDocumentVersion(
        document_id=UUID(int=number, version=4),
        tenant_id=tenant,
        version=version,
        document=DocumentInput(title="Runbook", text=text),
        chunking=build_character_chunking().chunk(
            ChunkingRequest(text=text, options=ChunkingOptions(chunk_size=1000, overlap=0))
        ),
    )


def search(reader, query, *, permissions=("document.search", "document.read"), **options):
    return build_bm25_retrieval(
        reader=reader, executor=PolicyEnforcedExecutor(PermissionPolicyService()), **options
    ).search(
        request=RetrievalRequest(query=query),
        context=ToolExecutionContext(tenant_id="a", permissions=frozenset(permissions)),
    )


def reader_for(*records):
    reader = Mock()
    reader.read_latest_documents.return_value = records
    return reader


def test_rare_word_beats_common_word_despite_equal_overlap():
    reader = reader_for(record(1, "webhook"), record(2, "idempotency"), record(3, "webhook"))
    assert search(reader, "webhook idempotency").passages[0].document_id == UUID(int=2, version=4)


def test_length_normalization_can_be_disabled():
    reader = reader_for(record(1, "timeout unrelated extra words"), record(2, "timeout"))
    assert search(reader, "timeout").passages[0].document_id == UUID(int=2, version=4)
    # Same frequency with b=0 means equal scores, resolved by document ID.
    assert search(reader, "timeout", b=0).passages[0].document_id == UUID(int=1, version=4)


def test_frequency_improves_rank_but_repeated_query_terms_do_not_change_results():
    reader = reader_for(record(1, "timeout other"), record(2, "timeout timeout"))
    result = search(reader, "TIMEOUT", b=0)
    assert result.passages[0].document_id == UUID(int=2, version=4)
    assert search(reader, "timeout timeout", b=0) == result
    # Frequency saturates: one rare matching term can outweigh many common repetitions.
    reader = reader_for(
        record(1, "common " * 100), record(2, "rare"), *(record(n, "common") for n in range(3, 10))
    )
    assert search(reader, "common rare", b=0).passages[0].document_id == UUID(int=2, version=4)


def test_empty_corpus_nonword_passages_and_semantic_misses():
    assert search(reader_for(), "timeout").passages == ()
    assert search(reader_for(record(1, "!!!")), "timeout").passages == ()
    assert (
        search(reader_for(record(1, "Refresh expired credentials")), "renew secrets").passages == ()
    )
    reader = reader_for()
    assert search(reader, "!!!").passages == ()
    reader.read_latest_documents.assert_not_called()


def test_denial_scope_errors_output_admission_and_storage_failure():
    reader = reader_for(record(1, "timeout", tenant="other"))
    with pytest.raises(PolicyDeniedError):
        search(reader, "timeout", permissions=())
    reader.read_latest_documents.assert_not_called()
    with pytest.raises(RetrievalResultError):
        search(reader, "timeout")
    with pytest.raises(RetrievalCapacityExceeded):
        search(reader_for(record(1, "timeout")), "timeout", max_output_chars=1)
    reader.read_latest_documents.side_effect = OSError("storage unavailable")
    with pytest.raises(OSError):
        search(reader, "timeout")


def test_real_store_excludes_other_tenants_old_versions_and_deleted_sources(tmp_path):
    store = build_sqlite_document_store(tmp_path / "docs.sqlite3")
    store.create_version(record(1, "old_timeout"))
    store.create_version(record(1, "current_timeout", version=2))
    store.create_version(record(2, "current_timeout", tenant="b"))
    assert search(store, "old_timeout").passages == ()
    hit = search(store, "current_timeout", max_documents=1).passages[0]
    assert hit.document_version == 2 and hit.chunk.text == "current_timeout"
    store.delete(tenant_id="a", document_id=hit.document_id)
    assert search(store, "current_timeout").passages == ()


@pytest.mark.parametrize(
    "options",
    [
        {"k1": 0},
        {"k1": float("nan")},
        {"k1": float("inf")},
        {"k1": True},
        {"k1": 101},
        {"b": -1},
        {"b": 2},
        {"b": "0.75"},
        {"max_documents": 0},
        {"max_snapshot_bytes": True},
        {"max_output_chars": 1.5},
    ],
)
def test_invalid_configuration_rejected(options):
    with pytest.raises(ValueError):
        BM25Retriever(Mock(), **options)
