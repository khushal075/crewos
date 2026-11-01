"""Bounded UTF-8 adapter; no filesystem, network, or document SDK dependency."""

from crewos.ingestion.document_parser import DocumentParseError


class PlainTextDocumentParser:
    def __init__(self, *, max_source_bytes: int = 4_000_000):
        if type(max_source_bytes) is not int or max_source_bytes < 1:
            raise ValueError("Source byte limit must be a positive integer")
        self.max_source_bytes = max_source_bytes

    def parse(self, content: bytes) -> str:
        if not isinstance(content, bytes):
            raise TypeError("Document content must be bytes")
        if len(content) > self.max_source_bytes:
            raise DocumentParseError("Document exceeds source byte limit")
        try:
            # A UTF-8 BOM is an encoding marker, not document text.
            return content.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            raise DocumentParseError("Plain-text content must use UTF-8 encoding") from None
