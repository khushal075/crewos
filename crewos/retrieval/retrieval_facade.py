"""Authorized public search entry point; no concrete search algorithm dependency."""

import logging
from time import monotonic

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.retrieval.passage_retriever import PassageRetrieverInterface, RetrievalResultError
from crewos.retrieval.search_contracts import RetrievalRequest, RetrievalResult


class RetrievalFacade:
    def __init__(
        self,
        *,
        retriever: PassageRetrieverInterface,
        executor: PolicyEnforcedExecutor,
        logger: logging.Logger | None = None,
    ):
        self._retriever, self._executor = retriever, executor
        self._logger = logger if logger is not None else logging.getLogger(__name__)

    def search(
        self, *, request: RetrievalRequest, context: ToolExecutionContext
    ) -> RetrievalResult:
        """Caller establishes authentication; both search and content read are required."""
        return self._executor.execute(
            context=context,
            action="document.search",
            operation=lambda: self._executor.execute(
                context=context,
                action="document.read",
                operation=lambda: self._search(request=request, tenant_id=context.tenant_id),
            ),
        )

    def _search(self, *, request, tenant_id):
        started = monotonic()
        self._logger.info({"event": "retrieval_started", "tenant_id": tenant_id})
        try:
            request = RetrievalRequest.model_validate(request.model_dump(warnings=False))
            result = self._retriever.search(request=request, tenant_id=tenant_id)
            if not isinstance(result, RetrievalResult):
                raise RetrievalResultError("Retriever must return a RetrievalResult")
            result = RetrievalResult.model_validate_json(result.model_dump_json(warnings=False))
            if len(result.passages) > request.top_k:
                raise RetrievalResultError("Retriever exceeded the requested passage limit")
        except Exception as error:
            self._logger.error(
                {
                    "event": "retrieval_failed",
                    "tenant_id": tenant_id,
                    "error_type": type(error).__name__,
                    "duration_ms": round((monotonic() - started) * 1000, 3),
                }
            )
            raise
        self._logger.info(
            {
                "event": "retrieval_succeeded",
                "tenant_id": tenant_id,
                "strategy": result.strategy,
                "passage_count": len(result.passages),
                "duration_ms": round((monotonic() - started) * 1000, 3),
            }
        )
        return result
