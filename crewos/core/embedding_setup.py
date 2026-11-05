"""Explicit embedding composition; existing retrieval does not invoke this factory."""

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
