"""Prepare untrusted document content using an injected parsing strategy."""

from crewos.ingestion.contracts import DocumentInput
from crewos.ingestion.document_parser import DocumentParserInterface


class DocumentPreparationService:
    def __init__(self, parser: DocumentParserInterface):
        self._parser = parser

    def prepare(
        self, *, content: bytes, title: str, source_ref: str | None = None
    ) -> DocumentInput:
        """Extract and validate content; parser and validation failures propagate.

        This is a preparation step, not an authorized ingestion endpoint: it
        assigns no document or tenant identity and persists nothing. Source
        references remain labels and document content remains untrusted.
        """
        text = self._parser.parse(content)
        return DocumentInput(title=title, text=text, source_ref=source_ref)
