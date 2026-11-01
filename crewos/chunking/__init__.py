"""Public chunking boundary for in-process callers and future transport adapters."""

from crewos.chunking.bootstrap import build_character_chunking
from crewos.chunking.contracts import ChunkingOptions, ChunkingRequest, ChunkingResult, TextChunk
from crewos.chunking.text_chunker import ChunkerInterface, ChunkingLimitExceeded
from crewos.chunking.service import ChunkingService

__all__ = [
    "TextChunk",
    "ChunkingOptions",
    "ChunkingRequest",
    "ChunkingResult",
    "ChunkerInterface",
    "ChunkingLimitExceeded",
    "ChunkingService",
    "build_character_chunking",
]
