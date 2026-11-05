"""Replaceable embedding provider boundary; no provider is implemented here."""

from typing import Protocol

from crewos.retrieval.embedding_contracts import EmbeddingBatch, TextEmbeddingRequest


class TextEmbeddingInterface(Protocol):
    def embed(self, *, request: TextEmbeddingRequest) -> EmbeddingBatch:
        """Return one vector per input in input order and validate_for(request).

        Use the same configured embedding space for corpus and query vectors.
        Model identity/revision and dimensions alone do not establish compatibility:
        preprocessing, task mode, normalization and provider configuration matter too.
        Do not silently truncate input or substitute another model on failure.
        Provider token limits may be smaller than the character admission limits.
        Propagate failures rather than representing them as an empty batch.

        Callers must authorize access before sending text to a provider. This
        protocol does not enforce permissions, privacy, ordering or provider behavior.
        """
        ...
