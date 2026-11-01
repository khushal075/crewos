"""Internal processing sequence; callers enter through DocumentIngestionFacade."""

from uuid import uuid4

from crewos.chunking import ChunkingOptions, ChunkingRequest, ChunkingService
from crewos.ingestion.contracts import DocumentIngestionRequest, StoredDocumentVersion
from crewos.ingestion.document_store import DocumentStoreInterface
from crewos.ingestion.service import DocumentPreparationService


class DocumentProcessingCoordinator:
    """Coordinate components after authorization; this is not an access gate."""

    def __init__(
        self,
        *,
        preparation: DocumentPreparationService,
        chunking: ChunkingService,
        store: DocumentStoreInterface,
        chunking_options: ChunkingOptions | None = None,
    ):
        self._preparation = preparation
        self._chunking = chunking
        self._store = store
        self._options = chunking_options if chunking_options is not None else ChunkingOptions()

    def process(
        self, *, request: DocumentIngestionRequest, tenant_id: str
    ) -> StoredDocumentVersion:
        """Use facade-established scope; failures stop the sequence and propagate."""
        request = DocumentIngestionRequest.model_validate(request.model_dump(warnings=False))
        document = self._preparation.prepare(
            content=request.content, title=request.title, source_ref=request.source_ref
        )
        chunks = self._chunking.chunk(ChunkingRequest(text=document.text, options=self._options))
        record = StoredDocumentVersion(
            document_id=uuid4(),
            tenant_id=tenant_id,
            version=1,
            document=document,
            chunking=chunks,
        )
        return self._store.create_version(record)
