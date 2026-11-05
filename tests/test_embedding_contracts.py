"""Embedding boundary validation without models, network or storage."""

import pytest
from pydantic import ValidationError

from crewos.retrieval.embedding_contracts import EmbeddingBatch, TextEmbeddingRequest
from crewos.retrieval.text_embedding import TextEmbeddingInterface


def batch(**changes):
    return EmbeddingBatch.model_validate(
        {
            "model_id": "example-model",
            "model_revision": "revision-1",
            "dimensions": 2,
            "vectors": [[1, 0.5], [-0.2, 1]],
            **changes,
        }
    )


def test_preserves_input_and_output_order_and_round_trips_json():
    request = TextEmbeddingRequest(texts=["  Timeout\n", "Expired token"])
    result = batch().validate_for(request)
    assert request.texts == ("  Timeout\n", "Expired token")
    assert result.vectors == ((1.0, 0.5), (-0.2, 1.0))
    assert EmbeddingBatch.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "texts", [[], [""], [" \n"], [123], [True], ["x" * 8193], ["x"] * 129, ["x" * 8192] * 9]
)
def test_rejects_invalid_or_over_capacity_inputs(texts):
    with pytest.raises(ValidationError):
        TextEmbeddingRequest(texts=texts)


def test_accepts_character_admission_boundary():
    assert len(TextEmbeddingRequest(texts=["x" * 8192] * 8).texts) == 8


@pytest.mark.parametrize(
    "changes",
    [
        {"model_id": " "},
        {"model_revision": ""},
        {"dimensions": True},
        {"dimensions": "2"},
        {"dimensions": 0},
        {"dimensions": 16385},
        {"vectors": []},
        {"vectors": [[1, 2]] * 129},
        {"vectors": [[]]},
        {"vectors": [[1] * 16385]},
        {"vectors": [[1], [1, 2]]},
        {"vectors": [["1", 2]]},
        {"vectors": [[True, 2]]},
        {"vectors": [[None, 2]]},
        {"vectors": [[float("nan"), 2]]},
        {"vectors": [[float("inf"), 2]]},
        {"vectors": [[float("-inf"), 2]]},
        {"vectors": [[10**400, 2]]},
        {"vectors": "invalid"},
        {"vectors": ["invalid"]},
        {"tenant_id": "claimed"},
    ],
)
def test_rejects_malformed_provider_results(changes):
    with pytest.raises(ValidationError):
        batch(**changes)


def test_count_is_checked_against_actual_request():
    with pytest.raises(ValueError, match="exactly one vector"):
        batch().validate_for(TextEmbeddingRequest(texts=["One input"]))


def test_request_rejects_authority_claims():
    with pytest.raises(ValidationError):
        TextEmbeddingRequest.model_validate({"texts": ["text"], "tenant_id": "claimed"})


def test_contracts_and_nested_vectors_are_immutable():
    request = TextEmbeddingRequest(texts=["text"])
    result = batch()
    with pytest.raises(ValidationError):
        request.texts = ("changed",)
    with pytest.raises(ValidationError):
        result.model_id = "changed"
    with pytest.raises(TypeError):
        result.vectors[0][0] = 5


def test_provider_can_satisfy_protocol_without_inheritance():
    class ExampleProvider:
        def embed(self, *, request: TextEmbeddingRequest) -> EmbeddingBatch:
            return batch(vectors=[[1, 0] for _ in request.texts]).validate_for(request)

    provider: TextEmbeddingInterface = ExampleProvider()
    request = TextEmbeddingRequest(texts=["sample"])
    assert provider.embed(request=request).vectors == ((1.0, 0.0),)
