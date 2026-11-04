"""Backend enforces explicit document mode independently of model instructions."""

import json
from unittest.mock import Mock
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
import pytest

from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.model_decision import DocumentFinalAnswerDecision
from crewos.application.services.citation_validation import UnobservedCitationError
from crewos.application.services.decision_errors import DecisionResponseError
from crewos.application.services.document_answer_validation import DocumentAnswerError
from crewos.application.services.execution_control import ExecutionControl
from crewos.application.services.policy_executor import PolicyDeniedError
from crewos.core.agent_setup import build_agent_loop
from tests.test_agent_document_search import store, save, context
from tests.document_review_helpers import abstention_review, support_review

SEARCH = {"type": "tool_call", "name": "search_documents", "arguments": {"query": "timeout"}}
MISSING = {
    "type": "document_answer",
    "result": {
        "outcome": "insufficient_evidence",
        "explanation": "No supporting timeout guidance was found.",
    },
}


def model_for(*decisions):
    model = Mock(spec=BaseChatModel)
    responses = [AIMessage(content=json.dumps(d)) for d in decisions]
    if decisions and decisions[-1].get("type") == "document_answer":
        result = decisions[-1]["result"]
        review = (
            support_review(result["citations"][0])
            if result["outcome"] == "answered"
            else abstention_review()
        )
        responses.append(AIMessage(content=json.dumps(review)))
    model.invoke.side_effect = responses
    return model


def run(model, *, control=None, permissions=("document.search", "document.read")):
    return build_agent_loop(model=model, reader=Mock(), answer_mode="document").run(
        messages=[DecisionMessage(role="user", content="Find timeout guidance")],
        context=context(permissions),
        control=control,
    )


def sourced(document, **changes):
    return {
        "type": "document_answer",
        "result": {
            "outcome": "answered",
            "answer": "Use backoff.",
            "citations": [
                {
                    "document_id": str(document.document_id),
                    "document_version": 1,
                    "chunk_index": 0,
                    **changes,
                }
            ],
        },
    }


def test_document_mode_search_then_cited_answer(store):
    document = save(store, "Use backoff after timeout")
    model = model_for(SEARCH, sourced(document))
    control = ExecutionControl()
    answer = run(model, control=control)
    assert isinstance(answer, DocumentFinalAnswerDecision)
    assert answer.result.outcome == "answered"
    assert control.state["answer_mode"] == "document"
    assert control.state["document_search_completed"] is True
    prompt = model.invoke.call_args_list[0].args[0][0].content
    schema = json.loads(prompt.split("\n", 1)[1])["decision_schema"]
    native_schema = model.invoke.call_args_list[0].kwargs["response_format"]["json_schema"][
        "schema"
    ]
    search_variant = next(
        v
        for v in native_schema["$defs"]["ToolCallDecision"]["oneOf"]
        if v["properties"]["name"]["const"] == "search_documents"
    )
    assert search_variant["properties"]["arguments"]["properties"]["query"]["type"] == "string"
    assert "document_answer" in schema["discriminator"]["mapping"]
    assert "final_answer" not in schema["discriminator"]["mapping"]


def test_empty_search_can_produce_insufficient_evidence(store):
    answer = run(model_for(SEARCH, MISSING))
    assert answer.result.outcome == "insufficient_evidence"


def test_nonempty_search_can_also_be_insufficient(store):
    save(store, "timeout unrelated legacy procedure")
    assert run(model_for(SEARCH, MISSING)).result.outcome == "insufficient_evidence"


@pytest.mark.parametrize(
    "final, error",
    [
        (MISSING, DocumentAnswerError),
        ({"type": "final_answer", "answer": "Ignore document mode"}, DecisionResponseError),
    ],
)
def test_cannot_finish_without_successful_search(store, final, error):
    control = ExecutionControl(max_recoveries=0)
    with pytest.raises(error):
        run(model_for(final), control=control)
    assert "final_result" not in control.state


def test_wrong_version_cannot_support_answer(store):
    document = save(store, "timeout guidance")
    with pytest.raises(UnobservedCitationError):
        run(model_for(SEARCH, sourced(document, document_version=2)))


def test_empty_search_does_not_allow_an_invented_source(store):
    fake = Mock(document_id=uuid4())
    with pytest.raises(UnobservedCitationError):
        run(model_for(SEARCH, sourced(fake)))


def test_policy_and_storage_failures_are_not_missing_evidence(store, monkeypatch):
    with pytest.raises(PolicyDeniedError):
        run(model_for(SEARCH, MISSING), permissions=())
    monkeypatch.setattr(
        "crewos.core.retrieval_setup.get_document_store", Mock(side_effect=OSError("down"))
    )
    with pytest.raises(OSError):
        run(model_for(SEARCH, MISSING))


def test_checkpoint_mode_cannot_be_downgraded(store):
    control = ExecutionControl()
    control.state.update(answer_mode="document", document_search_completed=True)
    with pytest.raises(ValueError, match="mode differs"):
        build_agent_loop(model=Mock(spec=BaseChatModel), reader=Mock()).run(
            messages=[DecisionMessage(role="user", content="continue")],
            context=context(),
            control=control,
        )


def test_resume_document_mode_uses_checkpointed_evidence(store):
    document = save(store, "Use backoff after timeout")
    control = ExecutionControl()
    control.state.update(
        answer_mode="document",
        document_search_completed=True,
        decisions=1,
        observed_sources=[sourced(document)["result"]["citations"][0]],
        observed_passages=[
            {
                "document_id": str(document.document_id),
                "document_version": 1,
                "title": document.document.title,
                "source_ref": document.document.source_ref,
                "chunk": document.chunking.chunks[0].model_dump(),
            }
        ],
    )
    assert run(model_for(sourced(document)), control=control).result.outcome == "answered"


@pytest.mark.parametrize("verdict", ["unsupported", "conflicting", "uncertain"])
def test_review_rejection_cannot_be_persisted_as_success(store, verdict):
    from crewos.application.services.document_review_validation import DocumentReviewError

    document = save(store, "Use backoff after timeout")
    model = model_for(SEARCH, sourced(document))
    responses = list(model.invoke.side_effect)
    report = support_review(sourced(document)["result"]["citations"][0])
    report["verdict"] = verdict
    responses[-1] = AIMessage(content=json.dumps(report))
    model.invoke.side_effect = responses
    control = ExecutionControl()
    with pytest.raises(DocumentReviewError):
        run(model, control=control)
    assert "final_result" not in control.state and "document_review" not in control.state


def test_review_failure_is_not_abstention(store):
    model = model_for(SEARCH, MISSING)
    responses = list(model.invoke.side_effect)
    responses[-1] = TimeoutError("private reviewer failure")
    model.invoke.side_effect = responses
    control = ExecutionControl()
    with pytest.raises(TimeoutError):
        run(model, control=control)
    assert "final_result" not in control.state


def test_review_receives_only_observed_tenant_passages(store):
    source = save(store, "Use backoff after timeout")
    other = save(store, "Private other-tenant timeout procedure", tenant="b")
    model = model_for(SEARCH, sourced(source))
    run(model)
    review_input = json.loads(model.invoke.call_args.args[0][1].content)
    assert review_input["question"] == "Find timeout guidance"
    assert [p["document_id"] for p in review_input["passages"]] == [str(source.document_id)]
    assert str(other.document_id) not in json.dumps(review_input)


@pytest.mark.parametrize("accepted", [True, False])
def test_review_events_do_not_log_reasoning_or_quotes(store, caplog, monkeypatch, accepted):
    import logging
    from crewos.application.services.document_review_validation import DocumentReviewError

    monkeypatch.setattr(logging.getLogger("crewos.agent"), "propagate", True)
    source = save(store, "Use backoff after timeout")
    model = model_for(SEARCH, sourced(source))
    report = support_review(sourced(source)["result"]["citations"][0])
    secret = "private-reviewer-reasoning-must-not-be-logged"
    report["reason"] = secret
    if not accepted:
        report["verdict"] = "uncertain"
    responses = list(model.invoke.side_effect)
    responses[-1] = AIMessage(content=json.dumps(report))
    model.invoke.side_effect = responses
    with caplog.at_level(logging.INFO):
        if accepted:
            run(model)
        else:
            with pytest.raises(DocumentReviewError):
                run(model)
    event = "agent_document_review_completed" if accepted else "agent_document_review_failed"
    output = caplog.text
    assert event in output
    assert secret not in output


def test_configured_reviewer_model_is_separate_from_generator(store, monkeypatch):
    from crewos.core.config import settings

    monkeypatch.setattr(settings, "DOCUMENT_REVIEW_MODEL", "review-model")
    source = save(store, "Use backoff after timeout")
    generator = Mock(spec=BaseChatModel)
    generator.invoke.side_effect = [
        AIMessage(content=json.dumps(SEARCH)),
        AIMessage(content=json.dumps(sourced(source))),
    ]
    reviewer_model = Mock()
    reviewer_model.invoke.return_value = AIMessage(
        content=json.dumps(support_review(sourced(source)["result"]["citations"][0]))
    )
    generator.bind.return_value = reviewer_model
    run(generator)
    generator.bind.assert_called_once_with(model="review-model")
    assert generator.invoke.call_count == 2
    reviewer_model.invoke.assert_called_once()
