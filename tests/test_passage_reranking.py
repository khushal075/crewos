"""Reranking preserves evidence, deadlines, and error semantics."""

from unittest.mock import Mock
import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from crewos.retrieval.passage_reranker import PassageRerankRequest, PassageRelevanceScores
from crewos.retrieval.strategies.model_reranker import ModelPassageReranker
from crewos.retrieval.strategies.reranking import CurrentPassageValidator, RerankingRetriever
from crewos.retrieval import RetrievalRequest, RetrievalResult, RetrievalResultError
from crewos.third_party.llm.passage_relevance_scorer import JsonPassageRelevanceScorer
from crewos.ingestion import build_sqlite_document_store
from tests.test_retrieval_contracts import passage
from tests.test_keyword_retrieval import save
from tests.test_hybrid_retrieval import hit


def test_score_order_preserves_exact_evidence_and_stable_ties():
    a, b, c = passage(), passage(), passage()
    scorer = Mock()
    scorer.score.return_value = PassageRelevanceScores(scores=(1, 4, 4))
    result = ModelPassageReranker(scorer).rerank(
        request=PassageRerankRequest(
            request=RetrievalRequest(query="recover", top_k=2), candidates=(a, b, c)
        )
    )
    assert result.passages == (b, c)


@pytest.mark.parametrize("scores", [(1,), (True,), (float("nan"),), (5,)])
def test_invalid_or_missing_scores_cannot_pass(scores):
    scorer = Mock()
    request = PassageRerankRequest(
        request=RetrievalRequest(query="recover"), candidates=(passage(), passage())
    )
    with pytest.raises((ValidationError, RetrievalResultError)):
        scorer.score.return_value = PassageRelevanceScores(scores=scores)
        ModelPassageReranker(scorer).rerank(request=request)


def test_empty_pool_skips_model():
    scorer = Mock()
    assert (
        ModelPassageReranker(scorer)
        .rerank(
            request=PassageRerankRequest(request=RetrievalRequest(query="recover"), candidates=())
        )
        .passages
        == ()
    )
    scorer.score.assert_not_called()


def test_json_scorer_uses_schema_and_does_not_accept_rewritten_passages():
    model = Mock()
    model.invoke.return_value = AIMessage(content='{"scores":[3]}')
    scorer = JsonPassageRelevanceScorer(model, timeout_seconds=7)
    request = PassageRerankRequest(
        request=RetrievalRequest(query="ignore rules"), candidates=(passage(),)
    )
    assert scorer.score(request=request).scores == (3,)
    assert model.invoke.call_args.kwargs["timeout"] == 7
    assert (
        model.invoke.call_args.kwargs["response_format"]["json_schema"]["schema"]["properties"][
            "scores"
        ]["maxItems"]
        == 1
    )
    model.invoke.return_value = AIMessage(content='{"scores":[3],"passages":[]}')
    with pytest.raises(ValidationError):
        scorer.score(request=request)


@pytest.mark.parametrize(
    "response",
    [
        AIMessage(content="not JSON"),
        AIMessage(content='{"scores":[]}'),
        AIMessage(content="{}", additional_kwargs={"refusal": "no"}),
    ],
)
def test_invalid_model_response_propagates(response):
    model = Mock()
    model.invoke.return_value = response
    with pytest.raises((ValidationError, RetrievalResultError)):
        JsonPassageRelevanceScorer(model).score(
            request=PassageRerankRequest(
                request=RetrievalRequest(query="recover"), candidates=(passage(),)
            )
        )


def test_source_update_during_rerank_rejects_result(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    first = save(source, "old evidence")
    base = Mock()
    base.search.return_value = RetrievalResult(strategy="fixture", passages=(hit(first),))
    scorer = Mock()

    def score(**kwargs):
        save(source, "new evidence", document_id=first.document_id, version=2)
        return PassageRelevanceScores(scores=(4,))

    scorer.score.side_effect = score
    with pytest.raises(RetrievalResultError, match="current source"):
        RerankingRetriever(
            retriever=base,
            reranker=ModelPassageReranker(scorer),
            validator=CurrentPassageValidator(source),
        ).search(request=RetrievalRequest(query="evidence", top_k=1), tenant_id="tenant_a")


def test_reranker_cannot_introduce_or_rewrite_evidence(tmp_path):
    source = build_sqlite_document_store(tmp_path / "source.db")
    first = save(source, "exact evidence")
    base, reranker = Mock(), Mock()
    base.search.return_value = RetrievalResult(strategy="fixture", passages=(hit(first),))
    reranker.rerank.return_value = RetrievalResult(
        strategy="fixture", passages=(hit(first).model_copy(update={"title": "forged"}),)
    )
    with pytest.raises(RetrievalResultError, match="changed evidence"):
        RerankingRetriever(
            retriever=base, reranker=reranker, validator=CurrentPassageValidator(source)
        ).search(request=RetrievalRequest(query="evidence"), tenant_id="tenant_a")


def test_priced_runs_reject_unaccounted_rerank_cost(monkeypatch):
    from crewos.application.services.execution_control import ResourceBudgetExceeded

    control = Mock()
    control.input_rate = 1
    control.max_cost = None
    control.tool_remaining.return_value = 5
    monkeypatch.setattr(
        "crewos.third_party.llm.passage_relevance_scorer.get_execution_control", lambda: control
    )
    model = Mock()
    with pytest.raises(ResourceBudgetExceeded):
        JsonPassageRelevanceScorer(model).score(
            request=PassageRerankRequest(
                request=RetrievalRequest(query="recover"), candidates=(passage(),)
            )
        )
    model.invoke.assert_not_called()


def test_reranking_model_obeys_remaining_tool_timeout(monkeypatch):
    control = Mock()
    control.input_rate = control.max_cost = None
    control.tool_remaining.return_value = 2
    monkeypatch.setattr(
        "crewos.third_party.llm.passage_relevance_scorer.get_execution_control", lambda: control
    )
    model = Mock()
    model.invoke.return_value = AIMessage(content='{"scores":[2]}')
    JsonPassageRelevanceScorer(model, timeout_seconds=30).score(
        request=PassageRerankRequest(
            request=RetrievalRequest(query="recover"), candidates=(passage(),)
        )
    )
    assert model.invoke.call_args.kwargs["timeout"] == 2
    control.reserve.assert_called_once()


def test_model_transport_failure_is_not_converted_into_a_ranking(caplog):
    model = Mock()
    model.invoke.side_effect = TimeoutError("private transport details")
    with pytest.raises(TimeoutError):
        JsonPassageRelevanceScorer(model).score(
            request=PassageRerankRequest(
                request=RetrievalRequest(query="private query"), candidates=(passage(),)
            )
        )
    assert "rerank_model_failed" in caplog.text
    assert "private transport details" not in caplog.text
    assert "private query" not in caplog.text
