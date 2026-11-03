"""Bounded in-memory BM25 over current tenant passages; no search index."""

from collections import Counter
from math import isfinite, log1p
import re

from crewos.ingestion import DocumentCorpusReaderInterface
from crewos.retrieval.passage_retriever import RetrievalCapacityExceeded, RetrievalResultError
from crewos.retrieval.search_contracts import RetrievalRequest, RetrievalResult, RetrievedPassage


class BM25Retriever:
    def __init__(
        self,
        reader: DocumentCorpusReaderInterface,
        *,
        k1: float = 1.2,
        b: float = 0.75,
        max_documents: int = 20,
        max_snapshot_bytes: int = 8_000_000,
        max_output_chars: int = 100_000,
    ):
        if any(
            type(v) is not int or v < 1
            for v in (max_documents, max_snapshot_bytes, max_output_chars)
        ):
            raise ValueError("Retrieval limits must be positive integers")
        if (
            type(k1) not in (int, float)
            or not isfinite(k1)
            or not 0 < k1 <= 100
            or type(b) not in (int, float)
            or not isfinite(b)
            or not 0 <= b <= 1
        ):
            raise ValueError("BM25 requires finite 0 < k1 <= 100 and 0 <= b <= 1")
        self._reader = reader
        self._k1, self._b = k1, b
        self._max_documents, self._max_bytes, self._max_output = (
            max_documents,
            max_snapshot_bytes,
            max_output_chars,
        )

    def search(self, *, request: RetrievalRequest, tenant_id: str) -> RetrievalResult:
        # Sorting gives reproducible floating-point summation across processes.
        terms = sorted(set(re.findall(r"\w+", request.query.casefold())))
        if not terms:
            return RetrievalResult(strategy="bm25_v1")
        documents = self._reader.read_latest_documents(
            tenant_id=tenant_id,
            max_documents=self._max_documents,
            max_snapshot_bytes=self._max_bytes,
        )
        passages = []
        for document in documents:
            if document.tenant_id != tenant_id:
                raise RetrievalResultError("Corpus reader returned another tenant's document")
            for chunk in document.chunking.chunks:
                counts = Counter(re.findall(r"\w+", chunk.text.casefold()))
                passages.append((document, chunk, counts, sum(counts.values())))
        total = len(passages)
        average_length = sum(p[3] for p in passages) / total if total else 0
        if not average_length:
            return RetrievalResult(strategy="bm25_v1")
        frequency = {term: sum(term in p[2] for p in passages) for term in terms}
        candidates = []
        for document, chunk, counts, length in passages:
            score = 0.0
            normalization = self._k1 * (1 - self._b + self._b * length / average_length)
            for term in terms:
                tf = counts[term]
                if tf:
                    idf = log1p((total - frequency[term] + 0.5) / (frequency[term] + 0.5))
                    score += idf * tf * (self._k1 + 1) / (tf + normalization)
            if score > 0:
                candidates.append((score, document, chunk))
        candidates.sort(key=lambda p: (-p[0], str(p[1].document_id), p[1].version, p[2].index))
        selected = candidates[: request.top_k]
        if sum(len(p[2].text) for p in selected) > self._max_output:
            raise RetrievalCapacityExceeded("Selected passages exceed output character limit")
        return RetrievalResult(
            strategy="bm25_v1",
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
