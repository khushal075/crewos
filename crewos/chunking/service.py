"""Application entry point for stateless, resource-bounded chunking."""

from crewos.chunking.contracts import ChunkingRequest, ChunkingResult
from crewos.chunking.text_chunker import ChunkerInterface, ChunkingLimitExceeded


class ChunkingService:
    def __init__(
        self,
        chunker: ChunkerInterface,
        *,
        max_chunks: int = 1000,
        max_output_chars: int = 2_000_000,
    ):
        if any(type(value) is not int or value < 1 for value in (max_chunks, max_output_chars)):
            raise ValueError("Chunking limits must be positive integers")
        self._chunker = chunker
        self._max_chunks = max_chunks
        self._max_output_chars = max_output_chars

    def chunk(self, request: ChunkingRequest) -> ChunkingResult:
        chunks = self._chunker.split(
            request, max_chunks=self._max_chunks, max_output_chars=self._max_output_chars
        )
        result = ChunkingResult(strategy=self._chunker.name, options=request.options, chunks=chunks)
        if (
            len(result.chunks) > self._max_chunks
            or sum(len(c.text) for c in result.chunks) > self._max_output_chars
        ):
            raise ChunkingLimitExceeded("Chunking exceeds configured output limits")
        if not result.chunks:
            raise ValueError("Chunker returned no chunks")
        previous_end = 0
        for index, chunk in enumerate(result.chunks):
            if (
                chunk.index != index
                or (index == 0 and chunk.start_char != 0)
                or chunk.start_char > previous_end
                or chunk.end_char <= previous_end
                or chunk.text != request.text[chunk.start_char : chunk.end_char]
            ):
                raise ValueError("Chunker returned inconsistent source spans")
            previous_end = chunk.end_char
        if previous_end != len(request.text):
            raise ValueError("Chunker did not cover the complete document")
        return result
