"""Replaceable splitting strategy; no dependency on ingestion or storage."""

from typing import Protocol

from crewos.chunking.contracts import ChunkingRequest, TextChunk


class ChunkingLimitExceeded(ValueError):
    """Requested splitting would exceed configured resource limits."""


class ChunkerInterface(Protocol):
    @property
    def name(self) -> str: ...

    def split(
        self, request: ChunkingRequest, *, max_chunks: int, max_output_chars: int
    ) -> tuple[TextChunk, ...]:
        """Preserve exact source spans and honor limits before allocating output."""
        ...
