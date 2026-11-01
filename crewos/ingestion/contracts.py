"""Ingestion-owned document and transient chunk contracts; content remains untrusted."""

from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=256)
    ]
    text: str = Field(strict=True, min_length=1, max_length=1_000_000)
    source_ref: (
        Annotated[
            str,
            StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2048),
        ]
        | None
    ) = None

    @field_validator("text")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Document text must contain non-whitespace characters")
        return value


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
