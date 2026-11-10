"""
    Caller-supplied data for registered a plain text source.
"""

from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

SourceTitle = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=256)
]

SourceReference = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2048)
]

_MAX_CONTENT_BYTES = 4_000_000

class SourceSubmission(BaseModel):
    """
    Content and metadata only; tenanat identity and channel comes from trusted code.
    """
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    title: SourceTitle
    content: str = Field(strict=True, min_length=1, max_length=_MAX_CONTENT_BYTES)
    source_reference: SourceReference | None = None

    @field_validator('content')
    @classmethod
    def check_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Content must contain non-whitespace text.")
        try:
            size = len(value.encode('utf-8'))
        except UnicodeDecodeError:
            raise ValueError("Content must be valid UTF-8 text.") from None

        if size > _MAX_CONTENT_BYTES:
            raise ValueError("Content exceeds the UTF-8 byte limit")
        return value