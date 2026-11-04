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
    model.invoke.side_effect = [AIMessage(content=json.dumps(d)) for d in decisions]
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
    document = save(store, "timeout guidance")
    control = ExecutionControl()
    control.state.update(
        answer_mode="document",
        document_search_completed=True,
        decisions=1,
        observed_sources=[sourced(document)["result"]["citations"][0]],
    )
    assert run(model_for(sourced(document)), control=control).result.outcome == "answered"
