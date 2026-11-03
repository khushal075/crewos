"""Small-corpus keyword baseline; no index, BM25, embeddings, or model calls."""

import re

from crewos.ingestion import DocumentCorpusReaderInterface
from crewos.retrieval.passage_retriever import RetrievalCapacityExceeded, RetrievalResultError
from crewos.retrieval.search_contracts import RetrievalRequest, RetrievalResult, RetrievedPassage


def _terms(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


class KeywordOverlapRetriever:
    def __init__(
        self,
        reader: DocumentCorpusReaderInterface,
        *,
        max_documents: int = 20,
        max_snapshot_bytes: int = 8_000_000,
        max_output_chars: int = 100_000,
    ):
        if any(
            type(value) is not int or value < 1
            for value in (max_documents, max_snapshot_bytes, max_output_chars)
        ):
            raise ValueError("Retrieval limits must be positive integers")
        self._reader = reader
        self._max_documents, self._max_bytes, self._max_output = (
            max_documents,
            max_snapshot_bytes,
            max_output_chars,
        )

    def search(self, *, request: RetrievalRequest, tenant_id: str) -> RetrievalResult:
        terms = _terms(request.query)
        if not terms:
            return RetrievalResult(strategy="keyword_overlap_v1")
        documents = self._reader.read_latest_documents(
            tenant_id=tenant_id,
            max_documents=self._max_documents,
            max_snapshot_bytes=self._max_bytes,
        )
        candidates = []
        for document in documents:
            if document.tenant_id != tenant_id:
                raise RetrievalResultError("Corpus reader returned another tenant's document")
            for chunk in document.chunking.chunks:
                score = len(terms & _terms(chunk.text))
                if score:
                    candidates.append((score, document, chunk))
        candidates.sort(
            key=lambda item: (-item[0], str(item[1].document_id), item[1].version, item[2].index)
        )
        selected = candidates[: request.top_k]
        if sum(len(item[2].text) for item in selected) > self._max_output:
            raise RetrievalCapacityExceeded("Selected passages exceed output character limit")
        return RetrievalResult(
            strategy="keyword_overlap_v1",
            passages=tuple(
                RetrievedPassage(
                    document_id=document.document_id,
                    document_version=document.version,
                    title=document.document.title,
                    source_ref=document.document.source_ref,
                    chunk=chunk,
                )
                for _, document, chunk in selected
            ),
        )
