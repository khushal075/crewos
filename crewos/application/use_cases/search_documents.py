"""Adapt validated agent tool arguments to the authorized retrieval facade."""

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.retrieval import (
    RetrievalCapacityExceeded,
    RetrievalFacade,
    RetrievalRequest,
    RetrievalResult,
)


class SearchDocumentsHandler:
    def __init__(self, *, retrieval: RetrievalFacade):
        self._retrieval = retrieval

    def execute(
        self, *, arguments: RetrievalRequest, context: ToolExecutionContext
    ) -> RetrievalResult:
        result = self._retrieval.search(request=arguments, context=context)
        # Include metadata and JSON encoding in the tool-observation admission limit.
        if len(result.model_dump_json().encode("utf-8")) > 32_000:
            raise RetrievalCapacityExceeded("Search observation exceeds serialized byte limit")
        return result
