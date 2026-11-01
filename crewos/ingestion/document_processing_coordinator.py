"""Internal processing sequence; callers enter through DocumentIngestionFacade."""

from uuid import UUID, uuid4

from crewos.chunking import ChunkingOptions, ChunkingRequest, ChunkingService
from crewos.ingestion.contracts import (
    DocumentIngestionRequest,
    DocumentUpdateRequest,
    StoredDocumentVersion,
)
from crewos.ingestion.document_store import DocumentStoreInterface, DocumentVersionConflict
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
        return self._prepare_and_store(
            request=request, tenant_id=tenant_id, document_id=uuid4(), version=1
        )

    def update(self, *, request: DocumentUpdateRequest, tenant_id: str) -> StoredDocumentVersion:
        """Check scoped latest revision before processing; storage fences races."""
        request = DocumentUpdateRequest.model_validate(request.model_dump(warnings=False))
        latest = self._store.get(tenant_id=tenant_id, document_id=request.document_id)
        if latest.version != request.expected_version:
            raise DocumentVersionConflict("Document has changed since the expected revision")
        return self._prepare_and_store(
            request=request,
            tenant_id=tenant_id,
            document_id=request.document_id,
            version=request.expected_version + 1,
        )

    def _prepare_and_store(
        self,
        *,
        request: DocumentIngestionRequest,
        tenant_id: str,
        document_id: UUID,
        version: int,
    ) -> StoredDocumentVersion:
        document = self._preparation.prepare(
            content=request.content, title=request.title, source_ref=request.source_ref
        )
        chunks = self._chunking.chunk(ChunkingRequest(text=document.text, options=self._options))
        record = StoredDocumentVersion(
            document_id=document_id,
            tenant_id=tenant_id,
            version=version,
            document=document,
            chunking=chunks,
        )
        return self._store.create_version(record)
