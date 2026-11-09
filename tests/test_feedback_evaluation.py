from uuid import uuid4

import pytest
from pydantic import ValidationError

from crewos.evaluation.feedback_cases import (
    FeedbackConflict,
    FeedbackExpectation,
    SQLiteFeedbackStore,
    evaluate_feedback,
    score,
)


def answer(text):
    return {"type": "final_answer", "answer": text}


def test_reviewed_feedback_compares_baseline_and_candidate_without_copying_content(tmp_path):
    store = SQLiteFeedbackStore(tmp_path / "feedback.db")
    case_a, case_b = str(uuid4()), str(uuid4())
    expectation = FeedbackExpectation(
        expected_outcome="final_answer",
        required_phrases=["verified"],
        forbidden_phrases=["unverified"],
    )
    store.add(
        tenant="tenant_a", run_id=case_a, expectation=expectation, baseline=answer("Verified")
    )
    store.add(
        tenant="tenant_a", run_id=case_b, expectation=expectation, baseline=answer("Unverified")
    )
    assert store.cases(tenant="tenant_b") == []
    report = evaluate_feedback(store.cases(tenant="tenant_a"), {case_b: answer("VERIFIED")})
    assert report["total"] == 2
    assert report["baseline_passed"] == report["candidate_passed"] == 1
    assert report["regressions"] == report["improvements"] == 1
    assert report["rows"][0]["candidate"]["failures"] == ["missing_candidate"]
    assert "verified" not in repr(report).lower()
    with pytest.raises(FeedbackConflict, match="already exists"):
        store.add(
            tenant="tenant_a", run_id=case_a, expectation=expectation, baseline=answer("changed")
        )
    with pytest.raises(ValueError, match="limit"):
        store.cases(tenant="tenant_a", limit=0)


def test_document_outcome_and_exact_citation_identity(tmp_path):
    ref = {"document_id": str(uuid4()), "document_version": 2, "chunk_index": 0}
    expectation = FeedbackExpectation.model_validate(
        {"expected_outcome": "answered", "required_citations": [ref]}
    )
    correct = {
        "type": "document_answer",
        "result": {"outcome": "answered", "answer": "supported", "citations": [ref]},
    }
    assert score(expectation, correct)["passed"]
    different_version = {
        **correct,
        "result": {**correct["result"], "citations": [{**ref, "document_version": 1}]},
    }
    assert score(expectation, different_version)["failures"] == ["missing_required_citation"]
    abstain = {
        "type": "document_answer",
        "result": {"outcome": "insufficient_evidence", "explanation": "Missing evidence"},
    }
    assert score(FeedbackExpectation(expected_outcome="insufficient_evidence"), abstain)["passed"]
    assert score(expectation, {"wrong": "shape"})["failures"] == ["invalid_candidate"]
    with pytest.raises(FeedbackConflict):
        SQLiteFeedbackStore(tmp_path / "feedback.db").add(
            tenant="tenant_a", run_id=str(uuid4()), expectation=expectation, baseline=answer("text")
        )


@pytest.mark.parametrize(
    "criteria",
    [
        {"expected_outcome": "answered"},
        {
            "expected_outcome": "final_answer",
            "required_phrases": ["Safe"],
            "forbidden_phrases": ["safe"],
        },
    ],
)
def test_reviewer_must_supply_consistent_criteria(criteria):
    with pytest.raises(ValidationError):
        FeedbackExpectation.model_validate(criteria)
