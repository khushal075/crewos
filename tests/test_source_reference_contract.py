"""Structured citation identities are bounded and validated independently of execution."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from crewos.application.dtos.model_decision import FinalAnswerDecision


def test_citation_contract_rejects_duplicate_malformed_and_excessive_references():
    citation = {"document_id": str(uuid4()), "document_version": 1, "chunk_index": 0}
    for citations in (
        [citation, citation],
        [{**citation, "chunk_index": -1}],
        [{**citation, "document_version": True}],
        [{**citation, "document_id": str(uuid4())} for _ in range(21)],
    ):
        with pytest.raises(ValidationError):
            FinalAnswerDecision(type="final_answer", answer="Answer", citations=citations)
