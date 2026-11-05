"""Explicit live embedding smoke test; no ingestion, storage or retrieval mutation."""

import argparse
import json
from math import hypot, isfinite
from time import monotonic

from crewos.retrieval.embedding_contracts import TextEmbeddingRequest
from crewos.retrieval.text_embedding import TextEmbeddingInterface
from crewos.third_party.llm.ollama_embedding_adapter import (
    EmbeddingProviderError,
    OllamaEmbeddingConfig,
    OllamaEmbeddingProvider,
)

# Explicit nomic task prefixes belong to this model-specific demo, not the generic adapter.
SYNTHETIC_TEXTS = (
    "search_document: An expired authentication token causes integration requests to fail.",
    "search_query: Why did the integration stop accepting credentials?",
)


def run_embedding_smoke(provider: TextEmbeddingInterface) -> dict:
    request = TextEmbeddingRequest(texts=SYNTHETIC_TEXTS)
    started = monotonic()
    batch = provider.embed(request=request).validate_for(request)
    norms = [hypot(*vector) for vector in batch.vectors]
    if any(not isfinite(norm) or norm == 0 for norm in norms):
        raise ValueError("Smoke check requires finite nonzero vector norms")
    return {
        "check": "embedding_inference_smoke",
        "model_id": batch.model_id,
        "configured_model_revision": batch.model_revision,
        "revision_verified_by_adapter": getattr(provider, "revision_verification", None)
        == "server_metadata_pre_and_post",
        "revision_verification": getattr(provider, "revision_verification", "not_reported"),
        "dimensions": batch.dimensions,
        "input_count": len(request.texts),
        "vector_count": len(batch.vectors),
        "all_vectors_nonzero": True,
        "elapsed_seconds": round(monotonic() - started, 6),
        "data": "synthetic integration example with explicit nomic task prefixes",
        "scope": "shape and inference only; not semantic relevance evaluation",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--revision",
        required=True,
        help="Expected full lowercase digest; checked against /api/tags before and after inference",
    )
    parser.add_argument("--dimensions", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)
    try:
        provider = OllamaEmbeddingProvider(
            config=OllamaEmbeddingConfig(
                base_url=args.base_url,
                model=args.model,
                model_revision=args.revision,
                dimensions=args.dimensions,
                timeout_seconds=args.timeout,
            )
        )
        report = run_embedding_smoke(provider)
    except (EmbeddingProviderError, ValueError):
        # Do not print provider/validation bodies, configuration credentials or vectors.
        print(json.dumps({"check": "embedding_inference_smoke", "status": "failed"}))
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
