"""Deterministic fixed-character windows with bounded overlap expansion."""

from crewos.chunking.contracts import ChunkingRequest, TextChunk
from crewos.chunking.ports import ChunkingLimitExceeded


class CharacterWindowChunker:
    name = "character_window_v1"

    def split(
        self, request: ChunkingRequest, *, max_chunks: int, max_output_chars: int
    ) -> tuple[TextChunk, ...]:
        length = len(request.text)
        size, overlap = request.options.chunk_size, request.options.overlap
        step = size - overlap
        count = 1 if length <= size else 1 + (length - size + step - 1) // step
        # Each source character appears once, plus the repeated overlaps.
        output_chars = length + (count - 1) * overlap
        if count > max_chunks or output_chars > max_output_chars:
            raise ChunkingLimitExceeded("Chunking exceeds configured output limits")
        return tuple(
            TextChunk(
                index=index,
                text=request.text[start : min(start + size, length)],
                start_char=start,
                end_char=min(start + size, length),
            )
            for index, start in enumerate(range(0, count * step, step))
        )
