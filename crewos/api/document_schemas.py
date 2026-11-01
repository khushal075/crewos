"""Plain-text HTTP payloads; source bytes remain an internal command detail."""

from pydantic import Field, field_validator
from crewos.ingestion import DocumentInput


class DocumentCreateBody(DocumentInput):
    @field_validator("text", "title", "source_ref")
    @classmethod
    def valid_utf8(cls, value):
        if value is None:
            return value
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Text must be valid UTF-8") from None
        return value


class DocumentUpdateBody(DocumentCreateBody):
    expected_version: int = Field(strict=True, ge=1)
