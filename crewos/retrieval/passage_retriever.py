"""Replaceable passage-search boundary with mandatory trusted tenant scope."""

from typing import Protocol
from crewos.retrieval.search_contracts import RetrievalRequest, RetrievalResult


class RetrievalResultError(RuntimeError):
    """A search implementation violated the application result contract."""


class RetrievalCapacityExceeded(RuntimeError):
    """The selected passages exceed configured output admission."""


class PassageRetrieverInterface(Protocol):
    def search(self, *, request: RetrievalRequest, tenant_id: str) -> RetrievalResult:
        """Search only current, available passages within the supplied tenant.

        Implementations must apply tenant scope before ranking/limiting results,
        honor top_k, and preserve exact source identity/text. Raise infrastructure
        failures; never disguise them as no matches. This interface does not
        itself enforce authorization or database isolation.
        """
        ...
