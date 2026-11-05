"""Real source/cache storage with controlled embeddings and authorization."""

from unittest.mock import Mock
from uuid import UUID

import pytest

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyDeniedError, PolicyEnforcedExecutor
from crewos.core.config import settings
from crewos.core.retrieval_setup import build_agent_retrieval
from crewos.ingestion import build_sqlite_document_store
from crewos.retrieval import RetrievalRequest, build_vector_retrieval
from crewos.retrieval.adapters.sqlite_chunk_embedding_store import SQLiteChunkEmbeddingStore
from crewos.retrieval.chunk_embedding_store import EmbeddingStorageLimitExceeded
from crewos.retrieval.document_embedding_indexer import DocumentEmbeddingIndexer
from crewos.retrieval.embedding_contracts import EmbeddingBatch
from crewos.retrieval.passage_retriever import RetrievalCapacityExceeded, RetrievalResultError
from crewos.retrieval.strategies.vector import VectorRetriever
from tests.test_embedding_storage_contracts import space
from tests.test_keyword_retrieval import save


class SemanticFixtureProvider:
    """Handwritten vectors for control-flow correctness, not model quality evidence."""

    def __init__(self):
        self.requests = []
        self.on_embed = None

    def embed(self, *, request):
        self.requests.append(request)
        if self.on_embed is not None:
            self.on_embed()
        vectors = []
        for text in request.texts:
            if any(term in text.lower() for term in ("expired", "credentials", "authentication")):
                vectors.append((1.0, 0.0))
            else:
                vectors.append((0.0, 1.0))
        return EmbeddingBatch(
            model_id="fixture-model", model_revision="fixture-digest", dimensions=2, vectors=vectors
        )


@pytest.fixture
def setup(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.sqlite3")
    cache = SQLiteChunkEmbeddingStore(tmp_path / "cache.sqlite3")
    provider = SemanticFixtureProvider()
    return source, cache, provider


def search(setup, *, permissions=("document.search", "document.read"), tenant="tenant_a", **limits):
    source, cache, provider = setup
    return build_vector_retrieval(
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


def test_returns_nearest_passage_and_reuses_stored_chunk_vectors(setup):
    source, _, provider = setup
    expected = save(source, "An expired authentication token causes failures.")
    save(source, "Python candidate experience.")
    first = search(setup)
    assert first.strategy == "vector_cosine_v1"
    assert first.passages[0].document_id == expected.document_id
    assert first.passages[0].chunk == expected.chunking.chunks[0]
    provider.requests.clear()
    assert search(setup) == first
    assert len(provider.requests) == 1  # query only; both source vectors persisted


def test_other_tenant_is_excluded_before_embedding_or_admission(setup):
    source, _, provider = setup
    own = save(source, "Python candidate experience.")
    save(source, "Expired authentication token secret tenant_b", tenant="tenant_b")
    result = search(setup, max_documents=1)
    assert result.passages[0].document_id == own.document_id
    assert all("tenant_b" not in text for request in provider.requests for text in request.texts)


@pytest.mark.parametrize("permissions", [(), ("document.read",), ("document.search",)])
def test_permission_denial_precedes_source_cache_and_provider(setup, permissions):
    source, cache, provider = setup
    reader = Mock(wraps=source)
    store = Mock(wraps=cache)
    with pytest.raises(PolicyDeniedError):
        search((reader, store, provider), permissions=permissions)
    reader.read_latest_documents.assert_not_called()
    store.read_document.assert_not_called()
    store.put.assert_not_called()
    assert provider.requests == []


def test_update_and_delete_exclude_stale_cached_vectors(setup):
    source, _, provider = setup
    first = save(source, "Expired authentication token.")
    assert search(setup).passages[0].document_version == 1
    save(source, "Python candidate experience.", document_id=first.document_id, version=2)
    current = search(setup)
    assert current.passages[0].document_version == 2
    assert current.passages[0].chunk.text == "Python candidate experience."
    source.delete(tenant_id="tenant_a", document_id=first.document_id)
    provider.requests.clear()
    assert search(setup).passages == ()
    assert provider.requests == []


def test_delete_during_inference_fails_closed(setup):
    source, _, provider = setup
    first = save(source, "Expired token.")
    deleted = False

    def delete_once():
        nonlocal deleted
        if not deleted:
            source.delete(tenant_id="tenant_a", document_id=first.document_id)
            deleted = True

    provider.on_embed = delete_once
    with pytest.raises(RetrievalResultError, match="changed during"):
        search(setup)
    assert search(setup).passages == ()


def test_chunk_and_input_admission_precede_provider(setup):
    source, _, provider = setup
    save(source, "x" * 150)
    with pytest.raises(RetrievalCapacityExceeded):
        search(setup, max_chunks=1)
    with pytest.raises(RetrievalCapacityExceeded):
        search(setup, max_embedding_input_bytes=1)
    assert provider.requests == []


def test_output_and_embedding_byte_limits_fail_instead_of_truncating(setup):
    source, _, _ = setup
    save(source, "Expired token.")
    with pytest.raises(RetrievalCapacityExceeded):
        search(setup, max_output_chars=1)
    with pytest.raises((RetrievalCapacityExceeded, EmbeddingStorageLimitExceeded)):
        search(setup, max_embedding_bytes=1)


def test_ties_have_stable_source_identity_order(setup):
    source, _, _ = setup
    save(source, "Expired token B.", document_id=UUID(int=2, version=4))
    first = save(source, "Expired token A.", document_id=UUID(int=1, version=4))
    assert search(setup).passages[0].document_id == first.document_id


def test_wrongly_scoped_source_reader_fails_before_provider(setup):
    source, cache, provider = setup
    foreign = save(source, "Expired token.", tenant="tenant_b")
    reader = Mock()
    reader.read_latest_documents.return_value = (foreign,)
    with pytest.raises(RetrievalResultError, match="another tenant"):
        search((reader, cache, provider))
    assert provider.requests == []


def test_invalid_similarity_scores_fail_explicitly(setup):
    source, cache, provider = setup
    save(source, "Expired token.")
    similarity = Mock()
    similarity.score.return_value = float("nan")
    retriever = VectorRetriever(
        source,
        indexer=DocumentEmbeddingIndexer(provider=provider, store=cache, space=space()),
        similarity=similarity,
    )
    with pytest.raises(RetrievalResultError, match="invalid score"):
        retriever.search(request=RetrievalRequest(query="credentials"), tenant_id="tenant_a")


def test_lazy_agent_vector_setup_cannot_open_database_before_authorization(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "RETRIEVAL_STRATEGY", "vector")
    monkeypatch.setattr(settings, "EMBEDDING_DB_PATH", str(tmp_path / "not-created.sqlite3"))
    facade = build_agent_retrieval()
    with pytest.raises(PolicyDeniedError):
        facade.search(
            request=RetrievalRequest(query="credentials"),
            context=ToolExecutionContext(tenant_id="tenant_a"),
        )
    assert not (tmp_path / "not-created.sqlite3").exists()


def test_agent_tool_dispatch_uses_configured_vector_retrieval(setup, monkeypatch):
    from crewos.core.tool_setup import build_tool_dispatcher

    source, cache, provider = setup
    expected = save(source, "Expired authentication token.")
    monkeypatch.setattr(settings, "RETRIEVAL_STRATEGY", "vector")
    monkeypatch.setattr(settings, "EMBEDDING_DB_PATH", str(cache.path))
    monkeypatch.setattr("crewos.core.retrieval_setup.get_document_store", lambda: source)
    monkeypatch.setattr("crewos.core.embedding_setup.build_embedding_space", lambda: space())
    monkeypatch.setattr(
        "crewos.core.embedding_setup.build_text_embedding_provider", lambda: provider
    )
    dispatcher = build_tool_dispatcher(reader=Mock())
    result = dispatcher.dispatch(
        name="search_documents",
        arguments={"query": "credentials", "top_k": 1},
        context=ToolExecutionContext(
            tenant_id="tenant_a", permissions=frozenset({"document.search", "document.read"})
        ),
    )
    assert result.passages[0].document_id == expected.document_id


def test_update_during_search_fails_closed(setup):
    source, _, provider = setup
    first = save(source, "Expired token.")
    changed = False

    def update_once():
        nonlocal changed
        if not changed:
            save(source, "Updated source.", document_id=first.document_id, version=2)
            changed = True

    provider.on_embed = update_once
    with pytest.raises(RetrievalResultError, match="changed during"):
        search(setup)


def test_provider_failure_does_not_fallback_to_keyword_search(setup):
    source, _, provider = setup
    save(source, "Expired token.")

    def fail():
        raise OSError("offline")

    provider.on_embed = fail
    with pytest.raises(OSError):
        search(setup)
