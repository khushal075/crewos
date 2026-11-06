"""Replaceable indexed keyword lookup; callers authorize before accessing it."""

from typing import Protocol
from crewos.ingestion import StoredDocumentVersion


class KeywordSearchIndexInterface(Protocol):
    def search_current_chunks(
        self,
        *,
        tenant_id: str,
        terms: tuple[str, ...],
        limit: int,
        max_snapshot_bytes: int = 8_000_000,
    ) -> tuple[tuple[StoredDocumentVersion, int], ...]:
        """Rank current source chunks in tenant scope before limiting; errors propagate."""
        ...
