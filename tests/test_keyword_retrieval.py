"""Storage-backed baseline verifies relevance ordering and actual tenant/source scope."""

import ast
from pathlib import Path
import sqlite3
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyDeniedError, PolicyEnforcedExecutor
from crewos.chunking import ChunkingOptions, ChunkingRequest, build_character_chunking
from crewos.ingestion import (
    DocumentCorpusLimitExceeded,
    DocumentCorpusReaderInterface,
    DocumentInput,
    DocumentStorageCorruption,
    StoredDocumentVersion,
    build_sqlite_document_store,
)
from crewos.retrieval import (
    RetrievalCapacityExceeded,
    RetrievalRequest,
    RetrievalResultError,
    build_keyword_retrieval,
)


def save(store, text, *, tenant="tenant_a", document_id=None, version=1, title="CRM runbook"):
    record = StoredDocumentVersion(
        tenant_id=tenant,
        document_id=document_id or uuid4(),
        version=version,
        document=DocumentInput(title=title, text=text, source_ref="runbook:crm"),
        chunking=build_character_chunking().chunk(
            ChunkingRequest(text=text, options=ChunkingOptions(chunk_size=100, overlap=0))
        ),
    )
    return store.create_version(record)


def search(
    reader,
    query,
    *,
    tenant="tenant_a",
    top_k=5,
    permissions=("document.search", "document.read"),
    **limits,
):
    return build_keyword_retrieval(
        reader=reader, executor=PolicyEnforcedExecutor(PermissionPolicyService()), **limits
    ).search(
        request=RetrievalRequest(query=query, top_k=top_k),
        context=ToolExecutionContext(tenant_id=tenant, permissions=frozenset(permissions)),
    )


@pytest.fixture
def store(tmp_path):
    return build_sqlite_document_store(tmp_path / "docs.sqlite3")


def test_distinct_query_word_overlap_ranks_above_repetition_and_preserves_evidence(store):
    weaker = save(store, "timeout timeout timeout", document_id=UUID(int=2, version=4))
    stronger = save(store, "webhook timeout", document_id=UUID(int=1, version=4))
    result = search(store, "WEBHOOK timeout timeout.", top_k=1)
    assert result.strategy == "keyword_overlap_v1"
    assert len(result.passages) == 1
    hit = result.passages[0]
    assert hit.document_id == stronger.document_id
    assert hit.document_version == 1 and hit.source_ref == "runbook:crm"
    assert hit.chunk.text == stronger.document.text[hit.chunk.start_char : hit.chunk.end_char]


def test_equal_scores_have_stable_document_and_chunk_order(store):
    second = save(store, "webhook recovery", document_id=UUID(int=2, version=4))
    first = save(store, "webhook handling", document_id=UUID(int=1, version=4))
    result = search(store, "webhook")
    assert [p.document_id for p in result.passages] == [first.document_id, second.document_id]
    assert search(store, "webhook") == result


def test_cross_tenant_words_never_enter_results_or_admission_counts(store):
    own = save(store, "webhook recovery")
    save(store, "webhook timeout recovery", tenant="tenant_b")
    save(store, "webhook timeout recovery", tenant="tenant_b")
    result = search(store, "webhook timeout", max_documents=1)
    assert [p.document_id for p in result.passages] == [own.document_id]
    assert search(store, "timeout").passages == ()


def test_only_latest_revision_is_searched_and_delete_removes_matches(store):
    first = save(store, "legacy_timeout")
    second = save(store, "current_recovery", document_id=first.document_id, version=2)
    assert search(store, "legacy_timeout").passages == ()
    result = search(store, "CURRENT_RECOVERY")
    assert result.passages[0].document_version == 2
    store.delete(tenant_id="tenant_a", document_id=first.document_id)
    assert search(store, "current_recovery").passages == ()


def test_permission_denial_prevents_corpus_read():
    reader = Mock(spec=DocumentCorpusReaderInterface)
    with pytest.raises(PolicyDeniedError):
        search(reader, "webhook", permissions=("document.read",))
    reader.read_latest_documents.assert_not_called()


def test_punctuation_query_has_no_terms_and_does_not_scan():
    reader = Mock(spec=DocumentCorpusReaderInterface)
    assert search(reader, "!!!").passages == ()
    reader.read_latest_documents.assert_not_called()


def test_words_are_casefolded_but_synonyms_and_title_only_matches_are_not_inferred(store):
    save(store, "RÉSUMÉ recovery", title="webhook outage")
    assert search(store, "résumé").passages
    assert search(store, "resume").passages == ()
    assert search(store, "webhook").passages == ()
    assert search(store, "outage").passages == ()


def test_corpus_admission_fails_before_snapshot_decode_instead_of_partial_search(
    store, monkeypatch
):
    save(store, "webhook one")
    save(store, "webhook two")
    decode = Mock(side_effect=AssertionError("Admission must precede decode"))
    monkeypatch.setattr(store, "_decode", decode)
    with pytest.raises(DocumentCorpusLimitExceeded):
        search(store, "webhook", max_documents=1)
    with pytest.raises(DocumentCorpusLimitExceeded):
        search(store, "webhook", max_snapshot_bytes=1)
    decode.assert_not_called()


def test_output_admission_rejects_excess_instead_of_truncating_evidence(store):
    save(store, "webhook recovery")
    with pytest.raises(RetrievalCapacityExceeded):
        search(store, "webhook", max_output_chars=1)


def test_corrupt_storage_and_reader_failures_propagate(store):
    save(store, "webhook")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE document_versions SET snapshot='invalid-private-snapshot'")
    with pytest.raises(DocumentStorageCorruption):
        search(store, "webhook")
    reader = Mock(spec=DocumentCorpusReaderInterface)
    reader.read_latest_documents.side_effect = sqlite3.OperationalError("Unavailable")
    with pytest.raises(sqlite3.OperationalError):
        search(reader, "webhook")


def test_strategy_rejects_wrong_tenant_from_a_misconfigured_reader(store):
    other = save(store, "webhook private", tenant="tenant_b")
    reader = Mock(spec=DocumentCorpusReaderInterface)
    reader.read_latest_documents.return_value = (other,)
    with pytest.raises(RetrievalResultError):
        search(reader, "webhook")


def test_corpus_uses_consistent_snapshot_when_delete_commits_during_read(store, monkeypatch):
    first = save(store, "webhook one", document_id=UUID(int=1, version=4))
    second = save(store, "webhook two", document_id=UUID(int=2, version=4))
    original = store._decode
    deleted = False

    def decode(row):
        nonlocal deleted
        result = original(row)
        if not deleted:
            deleted = True
            store.delete(tenant_id="tenant_a", document_id=second.document_id)
        return result

    monkeypatch.setattr(store, "_decode", decode)
    initial = store.read_latest_documents(
        tenant_id="tenant_a", max_documents=20, max_snapshot_bytes=8_000_000
    )
    assert {r.document_id for r in initial} == {first.document_id, second.document_id}
    subsequent = store.read_latest_documents(
        tenant_id="tenant_a", max_documents=20, max_snapshot_bytes=8_000_000
    )
    assert [r.document_id for r in subsequent] == [first.document_id]


@pytest.mark.parametrize(
    "limits", [{"max_documents": 0}, {"max_snapshot_bytes": True}, {"max_output_chars": -1}]
)
def test_invalid_strategy_limits_fail_at_setup(store, limits):
    with pytest.raises(ValueError):
        search(store, "webhook", **limits)


def test_corpus_reader_validates_scope_and_limits(store):
    with pytest.raises(ValueError):
        store.read_latest_documents(
            tenant_id="bad/tenant", max_documents=20, max_snapshot_bytes=1000
        )
    with pytest.raises(ValueError):
        store.read_latest_documents(
            tenant_id="tenant_a", max_documents=True, max_snapshot_bytes=1000
        )


def test_retrieval_uses_public_ingestion_boundary_and_hides_strategy_details():
    root = Path(__file__).resolve().parents[1] / "crewos"
    for path in (root / "retrieval").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            )
            assert not any(module.startswith("crewos.ingestion.") for module in modules)
