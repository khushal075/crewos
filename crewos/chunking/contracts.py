"""Serializable chunking boundary; positions count Python Unicode code points."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TextChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(strict=True, ge=0)
    text: str = Field(strict=True, min_length=1, max_length=1_000_000)
    start_char: int = Field(strict=True, ge=0, lt=1_000_000)
    end_char: int = Field(strict=True, gt=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_span(self) -> Self:
        if self.end_char - self.start_char != len(self.text):
            raise ValueError("Chunk span must match its text length")
        return self


class ChunkingOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_size: int = Field(default=1000, strict=True, ge=1, le=16384)
    overlap: int = Field(default=100, strict=True, ge=0)

    @model_validator(mode="after")
    def validate_overlap(self) -> Self:
        if self.overlap >= self.chunk_size:
            raise ValueError("Overlap must be smaller than chunk size")
        return self


class ChunkingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(strict=True, min_length=1, max_length=1_000_000)
    options: ChunkingOptions = Field(default_factory=ChunkingOptions)

    @field_validator("text")
    @classmethod
    def require_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Document text must contain non-whitespace characters")
        return value


class ChunkingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    strategy: str = Field(strict=True, min_length=1, max_length=128)
    options: ChunkingOptions
    chunks: tuple[TextChunk, ...]
