"""Remove a source from normal access within the caller's verified tenant."""

from crewos_ingestion.contracts.context_contracts.caller_context import CallerContext
from crewos_ingestion.contracts.input_contracts.source_lifecycle import SourceDeleteRequest
from crewos_ingestion.contracts.output_contracts.source_lifecycle import SourceDeleteReceipt
from crewos_ingestion.storage.source_store import SourceStore

SOURCE_DELETE_PERMISSION = "source.delete"


class SourceDeleteDenied(PermissionError):
    """
    The caller does not have permission to delete the source.
    """


class DeleteSource:

    def __init__(self, *, store: SourceStore) -> None:
        self._store = store

    def execute(
        self, *, request: SourceDeleteRequest, context: CallerContext
    ) -> SourceDeleteReceipt:
        if SOURCE_DELETE_PERMISSION not in context.permissions:
            raise SourceDeleteDenied("Caller lacks permission to delete a source")

        self._store.delete(
            tenant_id=context.tenant_id,
            source_id=request.source_id,
        )

        return SourceDeleteReceipt(source_id=request.source_id)
