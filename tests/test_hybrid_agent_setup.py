"""Agent configuration selects hybrid without bypassing the permission gate."""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.policy_executor import PolicyDeniedError
from crewos.core.config import Settings, settings
from crewos.core.retrieval_setup import build_agent_retrieval
from crewos.core.tool_setup import build_tool_dispatcher
from crewos.ingestion import build_sqlite_document_store
from crewos.retrieval import RetrievalRequest
from tests.test_embedding_storage_contracts import space
from tests.test_keyword_retrieval import save
from tests.test_vector_retrieval import SemanticFixtureProvider


def test_hybrid_settings_accept_environment_and_reject_invalid_values(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_STRATEGY", "hybrid")
    monkeypatch.setenv("HYBRID_CANDIDATE_K", "10")
    monkeypatch.setenv("HYBRID_RRF_RANK_CONSTANT", "30")
    config = Settings(_env_file=None)
    assert config.RETRIEVAL_STRATEGY == "hybrid"
    assert config.HYBRID_CANDIDATE_K == 10
    assert config.HYBRID_RRF_RANK_CONSTANT == 30
    for field, value in [("HYBRID_CANDIDATE_K", "21"), ("HYBRID_RRF_RANK_CONSTANT", "0")]:
        monkeypatch.setenv(field, value)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)
        monkeypatch.delenv(field)


@pytest.mark.parametrize("permissions", [(), ("document.search",), ("document.read",)])
def test_lazy_hybrid_denies_before_configuration_or_storage(monkeypatch, tmp_path, permissions):
    monkeypatch.setattr(settings, "RETRIEVAL_STRATEGY", "hybrid")
    path = tmp_path / "must-not-exist.db"
    monkeypatch.setattr(settings, "EMBEDDING_DB_PATH", str(path))
    provider_factory = Mock(side_effect=AssertionError("must not build provider"))
    reader = Mock(side_effect=AssertionError("must not read source"))
    monkeypatch.setattr(
        "crewos.core.embedding_setup.build_text_embedding_provider", provider_factory
    )
    monkeypatch.setattr("crewos.core.retrieval_setup.get_document_store", reader)
    with pytest.raises(PolicyDeniedError):
        build_agent_retrieval().search(
            request=RetrievalRequest(query="credentials"),
            context=ToolExecutionContext(tenant_id="tenant_a", permissions=frozenset(permissions)),
        )
    assert not path.exists()
    provider_factory.assert_not_called()
    reader.assert_not_called()


def test_agent_dispatch_selects_hybrid_and_applies_candidate_limit(monkeypatch, tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    expected = save(source, "Expired authentication token.")
    provider = SemanticFixtureProvider()
    monkeypatch.setattr(settings, "RETRIEVAL_STRATEGY", "hybrid")
    monkeypatch.setattr(settings, "HYBRID_CANDIDATE_K", 1)
    monkeypatch.setattr(settings, "HYBRID_RRF_RANK_CONSTANT", 30)
    monkeypatch.setattr(settings, "EMBEDDING_DB_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setattr("crewos.core.retrieval_setup.get_document_store", lambda: source)
    monkeypatch.setattr("crewos.core.embedding_setup.build_embedding_space", lambda: space())
    monkeypatch.setattr(
        "crewos.core.embedding_setup.build_text_embedding_provider", lambda: provider
    )
    dispatcher = build_tool_dispatcher(reader=Mock())
    context = ToolExecutionContext(
        tenant_id="tenant_a", permissions=frozenset({"document.read", "document.search"})
    )
    result = dispatcher.dispatch(
        name="search_documents", arguments={"query": "credentials", "top_k": 1}, context=context
    )
    # The registered tool uses the configured hybrid strategy and preserves evidence.
    assert result.strategy == "hybrid_fusion_v1"
    assert result.passages[0].document_id == expected.document_id
    from crewos.retrieval import RetrievalCapacityExceeded

    with pytest.raises(RetrievalCapacityExceeded):
        dispatcher.dispatch(
            name="search_documents", arguments={"query": "credentials", "top_k": 2}, context=context
        )
