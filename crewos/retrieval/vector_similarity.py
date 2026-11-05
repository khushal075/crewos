"""Replaceable vector comparison boundary, independent of storage and authorization."""

from typing import Protocol


class VectorSimilarityInterface(Protocol):
    def score(self, *, left: tuple[float, ...], right: tuple[float, ...]) -> float:
        """Compare compatible vectors; higher means more similar for this strategy.

        Callers must establish embedding-space compatibility and authorized scope.
        The score is not a correctness probability. Reject invalid inputs rather
        than silently substituting a score or truncating mismatched dimensions.
        """
        ...
