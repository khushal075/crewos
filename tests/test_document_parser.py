"""Parser substitution, source bounds, and normalized document validation."""

import pytest
from pydantic import ValidationError

from crewos.ingestion import DocumentInput
from crewos.ingestion import (
    DocumentParseError,
    DocumentParserInterface,
)
from crewos.ingestion.adapters.plain_text import PlainTextDocumentParser


def validate_document(parser: DocumentParserInterface, content: bytes) -> DocumentInput:
    return DocumentInput(title="CRM runbook", text=parser.parse(content))


def test_utf8_parser_preserves_unicode_spacing_and_newlines():
    text = "  Résumé recovery\r\n\n    retry()\n"
    assert validate_document(PlainTextDocumentParser(), text.encode("utf-8")).text == text


def test_strips_optional_utf8_encoding_marker_only():
    parser = PlainTextDocumentParser()
    assert parser.parse(b"\xef\xbb\xbf  Details\n") == "  Details\n"
    assert parser.parse("Details\ufeffend".encode()) == "Details\ufeffend"


def test_rejects_invalid_encoding_without_echoing_content():
    with pytest.raises(DocumentParseError) as error:
        PlainTextDocumentParser().parse(b"private-content\xff")
    assert "private-content" not in str(error.value)


def test_source_limit_counts_bytes_not_characters_and_accepts_exact_boundary():
    parser = PlainTextDocumentParser(max_source_bytes=4)
    assert parser.parse("éé".encode()) == "éé"
    with pytest.raises(DocumentParseError, match="byte limit"):
        parser.parse("ééa".encode())


@pytest.mark.parametrize("limit", [0, -1, True, "4", 1.5])
def test_invalid_byte_limits_are_rejected(limit):
    with pytest.raises(ValueError):
        PlainTextDocumentParser(max_source_bytes=limit)


@pytest.mark.parametrize("content", ["text", None, 123])
def test_requires_bytes(content):
    with pytest.raises(TypeError):
        PlainTextDocumentParser().parse(content)


@pytest.mark.parametrize("content", [b"", b" \n\t"])
def test_decoding_does_not_bypass_document_validation(content):
    with pytest.raises(ValidationError):
        validate_document(PlainTextDocumentParser(), content)


def test_application_can_accept_a_different_parser_without_changing_validation():
    class FixtureParser:
        def parse(self, content: bytes) -> str:
            return "Extracted incident details"

    assert validate_document(FixtureParser(), b"fixture").text == "Extracted incident details"
