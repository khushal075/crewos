"""Transient text segment; offsets address the prepared Python string."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
