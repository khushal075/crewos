"""Review acceptance requires matching provenance and evidence, not just a verdict."""

import json
from unittest.mock import Mock

from langchain_core.messages import AIMessage
import pytest

from crewos.application.dtos.document_review import DocumentReview, DocumentReviewRequest
from crewos.application.services.document_review_validation import (
    DocumentReviewError,
    review_receipt,
    validate_review_receipt,
    validate_document_review,
)
from crewos.application.services.decision_errors import DecisionResponseError, DecisionRefusedError
from crewos.application.services.execution_control import (
    ExecutionControl,
    ResourceBudgetExceeded,
    execution_scope,
)
from crewos.evaluation.document_answer_cases import cases
from crewos.evaluation.evaluate_document_answers import evaluate
from crewos.third_party.llm.document_answer_reviewer import JsonDocumentAnswerReviewer
from tests.document_review_helpers import support_review, abstention_review


def supported_request():
    return cases()[0].request


def supported_report():
    request = supported_request()
    data = support_review(
        request.answer.citations[0].model_dump(mode="json"), "retry after 30 seconds"
    )
    data["claims"][0]["claim"] = request.answer.answer
    return DocumentReview.model_validate(data)


def test_valid_review_receipt_is_bound_to_question_answer_and_passages():
    request, report = supported_request(), supported_report()
    receipt = review_receipt(request, report)
    validate_review_receipt(request, receipt)
    with pytest.raises(DocumentReviewError, match="does not match"):
        validate_review_receipt(
            request.model_copy(update={"question": "Different question"}), receipt
        )
    with pytest.raises(DocumentReviewError):
        validate_review_receipt(request, {})


@pytest.mark.parametrize(
    "field,value",
    [
        ("verdict", "unsupported"),
        ("verdict", "uncertain"),
        ("verdict", "conflicting"),
        ("evidence_answerability", "insufficient"),
        ("covers_all_claims", False),
        ("addresses_question", False),
        ("evidence_conflict", True),
        ("claims", []),
    ],
)
def test_accepting_envelope_is_not_enough(field, value):
    report = supported_report().model_dump(mode="json")
    report[field] = value
    with pytest.raises(DocumentReviewError):
        validate_document_review(supported_request(), DocumentReview.model_validate(report))


@pytest.mark.parametrize("change", ["quote", "source", "verdict", "excerpts"])
def test_claims_require_exact_cited_excerpts(change):
    data = supported_report().model_dump(mode="json")
    claim = data["claims"][0]
    if change == "quote":
        claim["excerpts"][0]["quote"] = "retry after 60 seconds"
    elif change == "source":
        claim["excerpts"][0]["source"]["document_version"] = 99
    elif change == "verdict":
        claim["verdict"] = "uncertain"
    else:
        claim["excerpts"] = []
    with pytest.raises(DocumentReviewError):
        validate_document_review(supported_request(), DocumentReview.model_validate(data))


def test_unnecessary_abstention_and_extra_claims_are_rejected():
    request = next(c.request for c in cases() if c.name == "empty_evidence")
    validate_document_review(request, DocumentReview.model_validate(abstention_review()))
    data = abstention_review()
    data["verdict"] = "unnecessary_abstention"
    with pytest.raises(DocumentReviewError):
        validate_document_review(request, DocumentReview.model_validate(data))
    data = abstention_review()
    data["claims"] = supported_report().model_dump(mode="json")["claims"]
    with pytest.raises(DocumentReviewError):
        validate_document_review(request, DocumentReview.model_validate(data))


def test_json_reviewer_honors_limits_usage_and_data_boundary():
    model = Mock()
    model.invoke.return_value = AIMessage(
        content=supported_report().model_dump_json(),
        response_metadata={"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}},
    )
    control = ExecutionControl()
    with execution_scope(control):
        actual = JsonDocumentAnswerReviewer(model, max_timeout_seconds=10).review(
            request=supported_request(), timeout_seconds=3
        )
    assert actual == supported_report()
    assert model.invoke.call_args.kwargs["timeout"] == 3
    assert model.invoke.call_args.kwargs["max_tokens"] == 2048
    response_format = model.invoke.call_args.kwargs["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert schema["properties"]["verdict"]["enum"] == [
        "supported",
        "unsupported",
        "conflicting",
        "uncertain",
    ]
    assert schema["$defs"] == DocumentReview.model_json_schema()["$defs"]
    messages = model.invoke.call_args.args[0]
    assert messages[0].type == "system" and messages[1].type == "human"
    assert json.loads(messages[1].content)["question"] == supported_request().question
    assert control.state["reserved_tokens"] > 0
    assert control.state["reported_input_tokens"] == 100


@pytest.mark.parametrize(
    "response,error",
    [
        (AIMessage(content="not JSON"), DecisionResponseError),
        (AIMessage(content="{}"), DecisionResponseError),
        (AIMessage(content="", additional_kwargs={"refusal": "no"}), DecisionRefusedError),
        (
            AIMessage(content="", tool_calls=[{"name": "unsafe", "args": {}, "id": "x"}]),
            DecisionResponseError,
        ),
    ],
)
def test_bad_review_output_never_accepts(response, error):
    model = Mock()
    model.invoke.return_value = response
    with pytest.raises(error):
        JsonDocumentAnswerReviewer(model).review(request=supported_request(), timeout_seconds=1)


def test_budget_exhaustion_and_provider_failure_propagate():
    model = Mock()
    reviewer = JsonDocumentAnswerReviewer(model)
    with execution_scope(ExecutionControl(max_tokens=1)):
        with pytest.raises(ResourceBudgetExceeded):
            reviewer.review(request=supported_request(), timeout_seconds=1)
    model.invoke.assert_not_called()
    model.invoke.side_effect = TimeoutError("private detail")
    with pytest.raises(TimeoutError):
        reviewer.review(request=supported_request(), timeout_seconds=1)


@pytest.mark.parametrize(
    "options",
    [
        {"max_timeout_seconds": float("nan")},
        {"max_timeout_seconds": 0},
        {"max_output_tokens": True},
    ],
)
def test_invalid_reviewer_configuration(options):
    with pytest.raises(ValueError):
        JsonDocumentAnswerReviewer(Mock(), **options)


@pytest.mark.parametrize("timeout", [0, True, float("inf")])
def test_invalid_review_deadline(timeout):
    with pytest.raises(ValueError):
        JsonDocumentAnswerReviewer(Mock()).review(
            request=supported_request(), timeout_seconds=timeout
        )


def test_large_input_rejected_before_model_call():
    model = Mock()
    data = supported_request().model_dump(mode="json")
    data["passages"] *= 100
    request = DocumentReviewRequest.model_validate(data)
    # Large original-question and passage payloads cannot silently bypass admission.
    data["question"] = "q" * 32768
    for item in data["passages"]:
        item["title"] = "t" * 256
        item["source_ref"] = "s" * 2048
    with pytest.raises(ValueError, match="byte admission"):
        JsonDocumentAnswerReviewer(model).review(
            request=DocumentReviewRequest.model_validate(data), timeout_seconds=1
        )
    model.invoke.assert_not_called()


def test_calibration_errors_count_as_errors_not_false_acceptances():
    reviewer = Mock()
    reviewer.review.side_effect = TimeoutError("private service text")
    report = evaluate(reviewer)
    assert report["total"] == 10 and report["completed"] == 0
    assert report["agreement_including_errors_as_incorrect"] == 0
    assert report["false_accepts"] == 0
    assert "private service text" not in json.dumps(report)


def test_calibration_false_acceptance_and_false_rejection_are_measured():
    reviewer = Mock()
    # A reviewer that abstains on every case wrongly accepts unnecessary abstention
    # and rejects the answerable supported case. It must not get a perfect score.
    reviewer.review.return_value = DocumentReview.model_validate(abstention_review())
    report = evaluate(reviewer)
    assert report["completed"] == 10
    assert report["false_accepts"] == 1 and report["false_rejections"] == 1
    assert report["agreement_including_errors_as_incorrect"] == 0.8


def test_fixture_inspection_cli_never_calls_model(monkeypatch, capsys):
    from crewos.evaluation.evaluate_document_answers import main

    model = Mock(side_effect=AssertionError("Offline inspection must not call a model"))
    monkeypatch.setattr("crewos.third_party.llm.ollama_adapter.OllamaLLMProvider.get", model)
    monkeypatch.setattr("sys.argv", ["evaluate_document_answers"])
    main()
    report = json.loads(capsys.readouterr().out)
    assert report["live_review_performed"] is False
    assert len(report["cases"]) == 10
    assert len({row["name"] for row in report["cases"]}) == 10
    model.assert_not_called()


@pytest.mark.parametrize(
    "errors,false_accepts,false_rejections", [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
)
def test_live_calibration_exit_status_reflects_errors_and_disagreement(
    monkeypatch, capsys, errors, false_accepts, false_rejections
):
    from crewos.evaluation.evaluate_document_answers import main

    model = Mock()
    monkeypatch.setattr(
        "crewos.third_party.llm.ollama_adapter.OllamaLLMProvider.get", lambda _: model
    )
    report = {
        "completed": 10 - errors,
        "total": 10,
        "false_accepts": false_accepts,
        "false_rejections": false_rejections,
    }
    monkeypatch.setattr(
        "crewos.evaluation.evaluate_document_answers.evaluate", lambda _, **kwargs: report
    )
    monkeypatch.setattr("sys.argv", ["evaluate_document_answers", "--live"])
    if errors or false_accepts or false_rejections:
        with pytest.raises(SystemExit) as exception:
            main()
        assert exception.value.code == 1
    else:
        main()
    assert json.loads(capsys.readouterr().out)["live_review_performed"] is True
    model.invoke.assert_not_called()


def test_answerable_evidence_cannot_accept_an_abstention_verdict():
    request = next(c.request for c in cases() if c.name == "unnecessary_abstention")
    report = abstention_review()
    report["evidence_answerability"] = "answerable"
    with pytest.raises(DocumentReviewError):
        validate_document_review(request, DocumentReview.model_validate(report))


@pytest.mark.parametrize("field", ["answer", "passage"])
def test_receipt_cannot_be_reused_for_changed_content(field):
    request = supported_request()
    receipt = review_receipt(request, supported_report())
    if field == "answer":
        changed = request.model_copy(
            update={"answer": request.answer.model_copy(update={"answer": "New answer"})}
        )
    else:
        passage = request.passages[0]
        changed = request.model_copy(
            update={"passages": (passage.model_copy(update={"title": "Changed source"}),)}
        )
    with pytest.raises(DocumentReviewError, match="does not match"):
        validate_review_receipt(changed, receipt)


def test_omitting_an_added_clause_is_rejected_despite_positive_coverage_flag():
    request = next(c.request for c in cases() if c.name == "partially_supported")
    data = supported_report().model_dump(mode="json")
    data["claims"][0]["claim"] = "Wait 30 seconds"
    with pytest.raises(DocumentReviewError, match="complete answer text"):
        validate_document_review(request, DocumentReview.model_validate(data))


def test_review_claims_cannot_paraphrase_away_the_answer_text():
    data = supported_report().model_dump(mode="json")
    data["claims"][0]["claim"] = "Retry later"
    with pytest.raises(DocumentReviewError, match="exact span"):
        validate_document_review(supported_request(), DocumentReview.model_validate(data))


def test_older_policy_receipts_cannot_bypass_current_review_checks():
    request = supported_request()
    receipt = review_receipt(request, supported_report())
    receipt["validation_policy_version"] = "older_policy"
    with pytest.raises(DocumentReviewError):
        validate_review_receipt(request, receipt)


def test_abstention_native_schema_prohibits_claim_list():
    model = Mock()
    model.invoke.return_value = AIMessage(content=json.dumps(abstention_review()))
    request = next(c.request for c in cases() if c.name == "empty_evidence")
    JsonDocumentAnswerReviewer(model).review(request=request, timeout_seconds=1)
    schema = model.invoke.call_args.kwargs["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["claims"]["maxItems"] == 0
