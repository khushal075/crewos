"""Cosine comparison with bounded validation and overflow-safe normalization."""

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
        left_unit = _unit_vector(left)
        right_unit = _unit_vector(right)
        if len(left_unit) != len(right_unit):
            raise ValueError("Vector dimensions must match")
        score = fsum(a * b for a, b in zip(left_unit, right_unit))
        # Floating-point rounding can otherwise put a unit-vector dot product just outside the range.
        return max(-1.0, min(1.0, score))
