"""Known geometry, numerical stability and invalid-input behavior."""

import pytest

from crewos.retrieval.strategies.cosine_similarity import CosineSimilarity
from crewos.retrieval.vector_similarity import VectorSimilarityInterface


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ((1.0, 0.0), (1.0, 0.0), 1.0),
        ((1.0, 0.0), (0.0, 1.0), 0.0),
        ((1.0, 0.0), (-1.0, 0.0), -1.0),
        ((1.0, 0.0), (1.0, 1.0), 2**-0.5),
        ((1.0, 2.0), (10.0, 20.0), 1.0),
        ((1e308, 1e308), (1e308, 1e308), 1.0),
        ((1e-320, 1e-320), (1e-320, 1e-320), 1.0),
        ((1e308, 1e308), (1e-320, -1e-320), 0.0),
    ],
)
def test_known_angles_and_magnitude_invariance(left, right, expected):
    strategy: VectorSimilarityInterface = CosineSimilarity()
    assert strategy.score(left=left, right=right) == pytest.approx(expected)
    assert strategy.score(left=right, right=left) == pytest.approx(expected)


@pytest.mark.parametrize(
    "invalid",
    [
        (),
        (0.0, 0.0),
        (True, 1.0),
        ("1", 0.0),
        (None, 1.0),
        (float("nan"), 1.0),
        (float("inf"), 1.0),
        (float("-inf"), 1.0),
        (10**400, 1.0),
        "12",
        None,
        (1.0,) * 16385,
    ],
)
def test_invalid_vectors_fail_explicitly(invalid):
    strategy = CosineSimilarity()
    with pytest.raises(ValueError):
        strategy.score(left=invalid, right=(1.0, 0.0))
    with pytest.raises(ValueError):
        strategy.score(left=(1.0, 0.0), right=invalid)


def test_mismatched_dimensions_are_not_silently_truncated():
    with pytest.raises(ValueError, match="dimensions must match"):
        CosineSimilarity().score(left=(1.0,), right=(1.0, 0.0))


def test_upper_dimension_boundary():
    vector = (1.0,) * 16384
    assert CosineSimilarity().score(left=vector, right=vector) == pytest.approx(1.0)
