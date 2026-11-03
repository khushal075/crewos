"""Retrieval boundaries preserve evidence identity and exclude authority claims."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from crewos.chunking import TextChunk
from crewos.retrieval import RetrievalRequest, RetrievedPassage, RetrievalResult


def passage(**changes):
    return RetrievedPassage.model_validate(
        {
            "document_id": uuid4(),
            "document_version": 2,
            "title": "CRM recovery",
            "source_ref": "runbook:crm",
            "chunk": TextChunk(index=0, text="Check logs.", start_char=0, end_char=11),
            **changes,
        }
    )


def test_query_normalizes_outer_whitespace_and_defaults_to_five_passages():
    request = RetrievalRequest(query="  How do I recover failed webhooks?  ")
    assert request.query == "How do I recover failed webhooks?"
    assert request.top_k == 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("query", " "),
        ("query", 123),
        ("query", "x" * 2049),
        ("top_k", 0),
        ("top_k", 21),
        ("top_k", True),
        ("top_k", "5"),
        ("tenant_id", "tenant_b"),
        ("permissions", ["document.read"]),
    ],
)
def test_rejects_invalid_requests_and_authority_claims(field, value):
    with pytest.raises(ValidationError):
        RetrievalRequest.model_validate({"query": "Find recovery instructions", field: value})


def test_result_round_trips_with_exact_source_version_and_positions():
    hit = passage()
    result = RetrievalResult(strategy="keyword_v1", passages=(hit,))
    restored = RetrievalResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.passages[0].document_version == 2
    assert restored.passages[0].chunk.start_char == 0
    assert restored.passages[0].chunk.end_char == 11
    assert restored.passages[0].source_ref == "runbook:crm"


def test_empty_results_are_valid_but_duplicate_source_identities_are_not():
    assert RetrievalResult(strategy="keyword_v1").passages == ()
    first = passage()
    with pytest.raises(ValidationError):
        RetrievalResult(strategy="keyword_v1", passages=(first, first))


def test_distinct_chunks_and_versions_of_one_document_have_distinct_identities():
    first = passage()
    second = passage(
        document_id=first.document_id,
        chunk=TextChunk(index=1, text="Retry.", start_char=12, end_char=18),
    )
    third = passage(document_id=first.document_id, document_version=3)
    assert (
        len(RetrievalResult(strategy="keyword_v1", passages=(first, second, third)).passages) == 3
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("document_id", "invalid"),
        ("document_version", 0),
        ("document_version", True),
        ("title", " "),
        ("source_ref", " "),
        ("tenant_id", "claimed"),
    ],
)
def test_rejects_malformed_passage_metadata(field, value):
    with pytest.raises(ValidationError):
        passage(**{field: value})


def test_result_bounds_count_and_rejects_undefined_strategy_labels():
    with pytest.raises(ValidationError):
        RetrievalResult(strategy="keyword_v1", passages=tuple(passage() for _ in range(21)))
    with pytest.raises(ValidationError):
        RetrievalResult(strategy=" ")


def test_contracts_are_immutable():
    request = RetrievalRequest(query="Find runbook")
    with pytest.raises(ValidationError):
        request.top_k = 20
    result = RetrievalResult(strategy="keyword_v1", passages=(passage(),))
    with pytest.raises(ValidationError):
        result.passages = ()
