"""Append a source revision within the caller's verified tenant."""

from crewos_ingestion.contracts.context_contracts.caller_context import CallerContext
from crewos_ingestion.contracts.input_contracts.source_lifecycle import SourceUpdateRequest
from crewos_ingestion.contracts.output_contracts.source_lifecycle import SourceUpdateReceipt
from crewos_ingestion.storage.source_store import SourceStore

SOURCE_UPDATE_PERMISSION = "source.update"


class SourceUpdateDenied(PermissionError):
    """The caller does not have permission to update sources."""


class UpdateSource:
    def __init__(self, *, store: SourceStore) -> None:
        self._store = store

    def execute(
        self, *, request: SourceUpdateRequest, context: CallerContext
    ) -> SourceUpdateReceipt:
        if SOURCE_UPDATE_PERMISSION not in context.permissions:
            raise SourceUpdateDenied("Caller lacks permission to update a source")

        stored = self._store.update(
            tenant_id=context.tenant_id,
            request=request,
        )
        return SourceUpdateReceipt(
            source_id=stored.source_id,
            version=stored.version,
        )
