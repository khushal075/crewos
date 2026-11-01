"""Wire the initial strategy without requiring a web server or worker."""

from crewos.chunking.service import ChunkingService
from crewos.chunking.strategies.character_window import CharacterWindowChunker


def build_character_chunking(
    *, max_chunks: int = 1000, max_output_chars: int = 2_000_000
) -> ChunkingService:
    return ChunkingService(
        CharacterWindowChunker(), max_chunks=max_chunks, max_output_chars=max_output_chars
    )
