"""Explicit embedding composition, also used by opt-in vector retrieval."""

from crewos.core.config import Settings, settings
from crewos.retrieval.text_embedding import TextEmbeddingInterface
from crewos.third_party.llm.ollama_embedding_adapter import (
    OllamaEmbeddingConfig,
    OllamaEmbeddingProvider,
)


def build_text_embedding_provider(config: Settings | None = None) -> TextEmbeddingInterface:
    source = config if config is not None else settings
    if (
        not source.EMBEDDING_MODEL
        or not source.EMBEDDING_MODEL_REVISION
        or not source.EMBEDDING_DIMENSIONS
    ):
        raise ValueError(
            "Configure embedding model, revision and dimensions before building a provider"
        )
    return OllamaEmbeddingProvider(
        config=OllamaEmbeddingConfig(
            base_url=source.EMBEDDING_BASE_URL,
            model=source.EMBEDDING_MODEL,
            model_revision=source.EMBEDDING_MODEL_REVISION,
            dimensions=source.EMBEDDING_DIMENSIONS,
            timeout_seconds=source.EMBEDDING_TIMEOUT,
        )
    )


def build_embedding_space(config: Settings | None = None):
    from crewos.retrieval.embedding_storage_contracts import EmbeddingSpace

    source = config if config is not None else settings
    model = source.EMBEDDING_MODEL
    revision = source.EMBEDDING_MODEL_REVISION
    dimensions = source.EMBEDDING_DIMENSIONS
    if not model or not revision or dimensions is None:
        raise ValueError(
            "Configure embedding model, revision and dimensions before building a space"
        )
    return EmbeddingSpace(
        provider_id="ollama_native_v1",
        model_id=model,
        model_revision=revision,
        dimensions=dimensions,
        preprocessing_revision="identity_v1",
        document_prefix=source.EMBEDDING_DOCUMENT_PREFIX,
        query_prefix=source.EMBEDDING_QUERY_PREFIX,
        normalization="provider",
    )
