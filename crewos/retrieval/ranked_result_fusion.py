"""Algorithm-independent contract for combining already-ranked search results."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from crewos.retrieval.search_contracts import RetrievalRequest, RetrievalResult


class RankedResultFusionRequest(BaseModel):
    """Ordered child results for one query within one already-authorized tenant.

    Each list is ordered best first. Empty child results are valid. Source order
    may be used for deterministic tie-breaking, so callers must keep it stable.
    The number of child lists is bounded independently of the output top_k.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    request: RetrievalRequest
    ranked_results: tuple[RetrievalResult, ...] = Field(min_length=1, max_length=8)


class RankedResultFusionInterface(Protocol):
    def fuse(self, *, request: RankedResultFusionRequest) -> RetrievalResult:
        """Combine supplied rankings without searching, model calls, or authorization.

        Return at most request.request.top_k distinct source passages. Identity is
        (document_id, document_version, chunk.index); the same passage can occur in
        multiple child lists. Preserve its exact source text and metadata. Reject
        conflicting payloads for the same identity with RetrievalResultError.

        Input ranks, rather than incomparable algorithm-specific scores, form this
        boundary. No relevant hits is a valid empty result; failures must propagate.
        Implementations must document their ranking and deterministic tie rules.

        The composing retriever must supply results from the same query and trusted
        tenant scope, apply any additional current-version/availability checks, and
        enforce resource limits. This contract cannot prove those conditions from
        result shapes and does not make separate backend reads a consistent snapshot.
        """
        ...
