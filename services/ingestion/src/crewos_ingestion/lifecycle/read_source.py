"""Read a source revision within the caller's verified tenant."""

from crewos_ingestion.contracts.context_contracts.caller_context import CallerContext
from crewos_ingestion.contracts.input_contracts.source_lifecycle import SourceReadRequest
from crewos_ingestion.contracts.output_contracts.source_lifecycle import SourceReadResult
from crewos_ingestion.storage.source_store import SourceStore

SOURCE_READ_PERMISSION = "source.read"


class SourceReadDenied(PermissionError):
    """The caller does not have permission to read sources."""


class ReadSource:
    def __init__(self, *, store: SourceStore) -> None:
        self._store = store

    def execute(self, *, request: SourceReadRequest, context: CallerContext) -> SourceReadResult:
        if SOURCE_READ_PERMISSION not in context.permissions:
            raise SourceReadDenied("Caller lacks permission to read a source")

        stored = self._store.read(
            tenant_id=context.tenant_id,
            source_id=request.source_id,
            version=request.version,
        )
        return SourceReadResult(
            source_id=stored.source_id,
            version=stored.version,
            title=stored.title,
            content=stored.content,
            source_reference=stored.source_reference,
        )
