"""Optional indexed/reranking modes remain inside the agent authorization gate."""

from unittest.mock import Mock
import pytest
from langchain_core.messages import AIMessage
from crewos.core.config import settings
from crewos.core.retrieval_setup import build_agent_retrieval
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.policy_executor import PolicyDeniedError
from crewos.retrieval import RetrievalRequest
from crewos.ingestion import build_sqlite_document_store
from tests.test_keyword_retrieval import save


@pytest.mark.parametrize("enabled", [True, False])
def test_denial_never_opens_source_or_builds_model(monkeypatch, enabled):
    monkeypatch.setattr(settings, "RETRIEVAL_STRATEGY", "indexed_bm25")
    monkeypatch.setattr(settings, "RERANKING_ENABLED", enabled)
    source = Mock(side_effect=AssertionError("must not open source"))
    monkeypatch.setattr("crewos.core.retrieval_setup.get_document_store", source)
    with pytest.raises(PolicyDeniedError):
        build_agent_retrieval().search(
            request=RetrievalRequest(query="token"),
            context=ToolExecutionContext(tenant_id="tenant_a"),
        )
    source.assert_not_called()


def test_configured_index_and_reranker_reach_model_after_policy(monkeypatch, tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    expected = save(source, "expired token")
    monkeypatch.setattr(settings, "RETRIEVAL_STRATEGY", "indexed_bm25")
    monkeypatch.setattr(settings, "RERANKING_ENABLED", True)
    monkeypatch.setattr(settings, "RERANKING_MODEL", "fixture-model")
    monkeypatch.setattr("crewos.core.retrieval_setup.get_document_store", lambda: source)
    model = Mock()
    model.bind.return_value = model
    model.invoke.return_value = AIMessage(content='{"scores":[4]}')
    monkeypatch.setattr(
        "crewos.third_party.llm.ollama_adapter.OllamaLLMProvider.get", lambda self: model
    )
    result = build_agent_retrieval().search(
        request=RetrievalRequest(query="token", top_k=1),
        context=ToolExecutionContext(
            tenant_id="tenant_a", permissions=frozenset({"document.search", "document.read"})
        ),
    )
    assert result.strategy == "reranked_retrieval_v1"
    assert result.passages[0].document_id == expected.document_id
    model.bind.assert_called_once_with(model="fixture-model")
