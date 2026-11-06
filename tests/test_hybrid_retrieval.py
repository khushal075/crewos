"""Hybrid composition preserves authorization, source scope and failure semantics."""

from unittest.mock import Mock

import pytest

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyDeniedError, PolicyEnforcedExecutor
from crewos.ingestion import build_sqlite_document_store
from crewos.retrieval import RetrievalRequest, RetrievalResult, build_hybrid_retrieval
from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
from crewos.retrieval.passage_retriever import RetrievalCapacityExceeded, RetrievalResultError
from crewos.retrieval.strategies.hybrid import HybridRetriever
from crewos.retrieval.strategies.reciprocal_rank_fusion import ReciprocalRankFusion
from crewos.retrieval.search_contracts import RetrievedPassage
from tests.test_embedding_storage_contracts import space
from tests.test_keyword_retrieval import save
from tests.test_vector_retrieval import SemanticFixtureProvider


@pytest.fixture
def setup(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    cache = SQLiteChunkEmbeddingStore(tmp_path / "vectors.db")
    return source, cache, SemanticFixtureProvider()


def search(setup, *, permissions=("document.read", "document.search"), tenant="tenant_a", **limits):
    source, cache, provider = setup
    return build_hybrid_retrieval(
        reader=source,
        store=cache,
        provider=provider,
        space=space(),
        executor=PolicyEnforcedExecutor(PermissionPolicyService()),
        **limits,
    ).search(
        request=RetrievalRequest(query="Why are credentials rejected?", top_k=1),
        context=ToolExecutionContext(tenant_id=tenant, permissions=frozenset(permissions)),
    )


def hit(document):
    return RetrievedPassage(
        document_id=document.document_id,
        document_version=document.version,
        title=document.document.title,
        source_ref=document.document.source_ref,
        chunk=document.chunking.chunks[0],
    )


def test_real_bm25_vector_fusion_and_cache_reuse(setup):
    source, _, provider = setup
    expected = save(source, "Expired authentication token.")
    save(source, "Python backend experience.")
    result = search(setup)
    assert result.strategy == "hybrid_fusion_v1"
    assert result.passages == (hit(expected),)
    provider.requests.clear()
    assert search(setup) == result
    assert len(provider.requests) == 1


@pytest.mark.parametrize("permissions", [(), ("document.search",), ("document.read",)])
def test_denial_before_source_cache_or_model(setup, permissions):
    source, cache, provider = setup
    reader, store = Mock(wraps=source), Mock(wraps=cache)
    with pytest.raises(PolicyDeniedError):
        search((reader, store, provider), permissions=permissions)
    reader.read_latest_documents.assert_not_called()
    store.read_document.assert_not_called()
    assert provider.requests == []


def test_tenant_scope_before_admission_and_embedding(setup):
    source, _, provider = setup
    own = save(source, "Python experience.")
    save(source, "Expired credentials tenant_b_secret", tenant="tenant_b")
    result = search(setup, max_documents=1)
    assert result.passages == (hit(own),)
    assert all("tenant_b_secret" not in text for req in provider.requests for text in req.texts)


def test_empty_tenant_skips_provider(setup):
    assert search(setup).passages == ()
    assert setup[2].requests == []


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_source_change_during_embedding_rejects_mixed_results(setup, operation):
    source, _, provider = setup
    first = save(source, "Expired token.")

    def change():
        provider.on_embed = None
        if operation == "update":
            save(source, "Replacement content.", document_id=first.document_id, version=2)
        else:
            source.delete(tenant_id="tenant_a", document_id=first.document_id)

    provider.on_embed = change
    with pytest.raises(RetrievalResultError, match="Source corpus changed"):
        search(setup)


def test_provider_failure_propagates_without_silent_keyword_fallback(setup):
    source, cache, _ = setup
    save(source, "credentials rejected")
    provider = Mock()
    provider.embed.side_effect = OSError("provider unavailable")
    with pytest.raises(OSError):
        search((source, cache, provider))


def test_children_share_snapshot_and_receive_deeper_candidate_request(setup):
    source, _, _ = setup
    first = save(source, "webhook timeout")
    reader = Mock(wraps=source)
    requests = []

    def children(snapshot):
        def run(*, request, tenant_id):
            requests.append(request)
            assert snapshot.read_latest_documents(
                tenant_id=tenant_id, max_documents=20, max_snapshot_bytes=8_000_000
            ) == (first,)
            return RetrievalResult(strategy="fixture", passages=(hit(first),))

        return (Mock(search=run), Mock(search=run))

    result = HybridRetriever(
        reader=reader, children_factory=children, fusion=ReciprocalRankFusion()
    ).search(request=RetrievalRequest(query="webhook", top_k=1), tenant_id="tenant_a")
    assert result.passages == (hit(first),)
    assert [req.top_k for req in requests] == [20, 20]
    assert all(req.query == "webhook" for req in requests)
    assert reader.read_latest_documents.call_count == 2


@pytest.mark.parametrize("kind", ["forged", "wrong_version", "invalid_type", "too_many"])
def test_invalid_child_results_fail_closed(setup, kind):
    source, _, _ = setup
    first = save(source, "webhook timeout")
    second = save(source, "webhook retry")
    result = {
        "forged": RetrievalResult(
            strategy="fixture", passages=(hit(first).model_copy(update={"title": "Forged"}),)
        ),
        "wrong_version": RetrievalResult(
            strategy="fixture", passages=(hit(first).model_copy(update={"document_version": 99}),)
        ),
        "invalid_type": {},
        "too_many": RetrievalResult(strategy="fixture", passages=(hit(first), hit(second))),
    }[kind]
    child = Mock()
    child.search.return_value = result
    fusion = Mock()
    with pytest.raises(RetrievalResultError):
        HybridRetriever(
            reader=source,
            children_factory=lambda reader: (child, child),
            fusion=fusion,
            candidate_k=1,
        ).search(request=RetrievalRequest(query="webhook", top_k=1), tenant_id="tenant_a")
    fusion.fuse.assert_not_called()


def test_fusion_cannot_introduce_unretrieved_source_passages(setup):
    source, _, _ = setup
    first = save(source, "webhook timeout")
    absent = save(source, "not retrieved")
    child = Mock()
    child.search.return_value = RetrievalResult(strategy="fixture", passages=(hit(first),))
    fusion = Mock()
    fusion.fuse.return_value = RetrievalResult(strategy="fixture", passages=(hit(absent),))
    with pytest.raises(RetrievalResultError, match="absent from child"):
        HybridRetriever(
            reader=source, children_factory=lambda reader: (child, child), fusion=fusion
        ).search(request=RetrievalRequest(query="webhook"), tenant_id="tenant_a")


def test_reader_cannot_supply_other_tenant(setup):
    source, _, provider = setup
    other = save(source, "private", tenant="tenant_b")
    reader = Mock()
    reader.read_latest_documents.return_value = (other,)
    with pytest.raises(RetrievalResultError, match="another tenant"):
        search((reader, setup[1], provider))
    assert not provider.requests


@pytest.mark.parametrize(
    "limits", [{"max_documents": 0}, {"max_chunks": True}, {"candidate_k": 21}]
)
def test_invalid_limits(limits):
    with pytest.raises(ValueError):
        HybridRetriever(reader=Mock(), children_factory=Mock(), fusion=Mock(), **limits)


def test_top_k_cannot_exceed_candidate_depth():
    reader = Mock()
    with pytest.raises(RetrievalCapacityExceeded):
        HybridRetriever(
            reader=reader, children_factory=Mock(), fusion=Mock(), candidate_k=1
        ).search(request=RetrievalRequest(query="webhook", top_k=2), tenant_id="tenant_a")
    reader.read_latest_documents.assert_not_called()
