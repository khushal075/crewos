"""Cosine comparison with bounded validation and overflow-safe normalization."""

from collections.abc import Callable
from math import fsum, hypot, isfinite


def _unit_vector(vector: tuple[float, ...]) -> tuple[float, ...]:
    if not isinstance(vector, (tuple, list)) or not 1 <= len(vector) <= 16384:
        raise ValueError("Vectors require 1 to 16384 coordinates")
    converted = []
    for value in vector:
        if type(value) not in (int, float):
            raise ValueError("Vector coordinates must be numbers, excluding booleans")
        try:
            number = float(value)
        except OverflowError:
            raise ValueError("Vector coordinates must be finite") from None
        if not isfinite(number):
            raise ValueError("Vector coordinates must be finite")
        converted.append(number)
    scale = max(abs(value) for value in converted)
    if scale == 0:
        raise ValueError("Cosine similarity is undefined for a zero vector")
    scaled = tuple(value / scale for value in converted)
    norm = hypot(*scaled)
    return tuple(value / norm for value in scaled)


class CosineSimilarity:
    def score(self, *, left: tuple[float, ...], right: tuple[float, ...]) -> float:
        """Compare direction, ignoring positive magnitude; result lies in [-1, 1]."""
        return self.prepare(left)(right)

    def prepare(self, query: tuple[float, ...]) -> Callable[[tuple[float, ...]], float]:
        """Normalize the query once for a batch of exact comparisons."""
        left_unit = _unit_vector(query)

        def compare(candidate: tuple[float, ...]) -> float:
            right_unit = _unit_vector(candidate)
            if len(left_unit) != len(right_unit):
                raise ValueError("Vector dimensions must match")
            score = fsum(a * b for a, b in zip(left_unit, right_unit))
            return max(-1.0, min(1.0, score))

        return compare
