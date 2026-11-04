"""Answer shape checks distinguish missing evidence from an asserted sourced answer."""

from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
import pytest

from crewos.application.dtos.document_answer import (
    AnsweredDocumentAnswer,
    DocumentAnswer,
    DocumentAnswerOutcome,
    InsufficientEvidenceAnswer,
)

adapter = TypeAdapter(DocumentAnswer)


def citation():
    return {"document_id": str(uuid4()), "document_version": 1, "chunk_index": 0}


def test_answered_branch_preserves_source_identity_and_round_trips():
    source = citation()
    result = adapter.validate_python(
        {
            "outcome": "answered",
            "answer": " Use backoff. ",
            "citations": [source],
        }
    )
    assert isinstance(result, AnsweredDocumentAnswer)
    assert result.outcome == DocumentAnswerOutcome.ANSWERED
    assert result.answer == "Use backoff."
    assert str(result.citations[0].document_id) == source["document_id"]
    assert adapter.validate_json(result.model_dump_json()) == result


def test_missing_evidence_branch_has_explanation_not_an_answer_or_citations():
    result = adapter.validate_python(
        {
            "outcome": "insufficient_evidence",
            "explanation": "The retrieved passages describe retries but not credential rotation.",
        }
    )
    assert isinstance(result, InsufficientEvidenceAnswer)
    assert adapter.validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "changes",
    [
        {"citations": []},
        {"answer": " "},
        {"answer": 123},
        {"answer": "x" * 8193},
        {"citations": [citation() for _ in range(21)]},
        {"tenant_id": "other"},
    ],
)
def test_answered_branch_rejects_missing_evidence_and_invalid_fields(changes):
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"outcome": "answered", "answer": "Use backoff", "citations": [citation()], **changes}
        )


def test_duplicate_references_do_not_satisfy_multiple_citations():
    source = citation()
    with pytest.raises(ValidationError, match="distinct"):
        adapter.validate_python(
            {"outcome": "answered", "answer": "Use backoff", "citations": [source, source]}
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"explanation": " "},
        {"explanation": 123},
        {"explanation": "x" * 2049},
        {"answer": "An unsupported answer"},
        {"citations": [citation()]},
        {"outcome": "storage_failed"},
    ],
)
def test_insufficient_branch_cannot_smuggle_answer_fields_or_operational_outcomes(changes):
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"outcome": "insufficient_evidence", "explanation": "No supporting passages", **changes}
        )


def test_outcome_is_required_and_contract_is_immutable():
    with pytest.raises(ValidationError):
        adapter.validate_python({"explanation": "No evidence"})
    result = InsufficientEvidenceAnswer(outcome="insufficient_evidence", explanation="No evidence")
    with pytest.raises(ValidationError):
        result.explanation = "changed"
