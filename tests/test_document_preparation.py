"""Preparation composes extraction and validation without a concrete-parser dependency."""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from crewos.application.interfaces.document_parser import (
    DocumentParseError,
    DocumentParserInterface,
)
from crewos.application.services.document_preparation import DocumentPreparationService
from crewos.infrastructure.plain_text_document_parser import PlainTextDocumentParser


def test_prepares_utf8_runbook_preserving_text_and_source_reference():
    service = DocumentPreparationService(PlainTextDocumentParser())
    text = "  CRM runbook\n\n    Check delivery logs.\n"
    result = service.prepare(
        content=text.encode(), title=" CRM recovery ", source_ref=" runbook:v1 "
    )
    assert result.text == text
    assert result.title == "CRM recovery"
    assert result.source_ref == "runbook:v1"
    assert "tenant_id" not in result.model_dump()


def test_accepts_an_alternative_parsing_strategy_and_optional_source():
    class FixtureParser:
        def parse(self, content: bytes) -> str:
            return "Extracted from a different format"

    result = DocumentPreparationService(FixtureParser()).prepare(
        content=b"fixture", title="Incident"
    )
    assert result.text == "Extracted from a different format"
    assert result.source_ref is None


@pytest.mark.parametrize("text", [" \n", "x" * 1_000_001, 123])
def test_parser_output_must_satisfy_document_contract(text):
    parser = Mock(spec=DocumentParserInterface)
    parser.parse.return_value = text
    with pytest.raises(ValidationError):
        DocumentPreparationService(parser).prepare(content=b"source", title="Runbook")


def test_invalid_metadata_cannot_bypass_contract():
    with pytest.raises(ValidationError):
        DocumentPreparationService(PlainTextDocumentParser()).prepare(content=b"Details", title=" ")


def test_parse_failure_propagates_without_returning_a_partial_document():
    service = DocumentPreparationService(PlainTextDocumentParser())
    with pytest.raises(DocumentParseError):
        service.prepare(content=b"\xff", title="Runbook")


def test_unexpected_parser_failure_is_not_disguised_as_success():
    parser = Mock(spec=DocumentParserInterface)
    parser.parse.side_effect = RuntimeError("Parser unavailable")
    with pytest.raises(RuntimeError, match="Parser unavailable"):
        DocumentPreparationService(parser).prepare(content=b"source", title="Runbook")
