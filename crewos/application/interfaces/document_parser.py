"""Format-specific extraction boundary; parsing never grants content authority."""

from typing import Protocol


class DocumentParseError(ValueError):
    """The supplied content cannot be extracted under the parser's constraints."""


class DocumentParserInterface(Protocol):
    def parse(self, content: bytes) -> str:
        """Extract text from already-loaded bytes without fetching sources.

        Implementations must bound input size and report unsupported or malformed
        content as DocumentParseError without including source content in errors.
        Returned text is untrusted; DocumentInput validates it downstream.
        Authentication, authorization, storage, and chunking belong outside parsers.
        """
        ...
