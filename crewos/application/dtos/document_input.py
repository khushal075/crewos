"""Untrusted plain-text ingestion input; identity comes from authenticated context."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


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
