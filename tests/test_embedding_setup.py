"""Configuration identity is explicit and preserves model-specific prefixes."""

import pytest

from crewos.core.config import Settings
from crewos.core.embedding_setup import build_embedding_space


def settings(**changes):
    return Settings(
        _env_file=None,
        **{
            "EMBEDDING_MODEL": "fixture:tag",
            "EMBEDDING_MODEL_REVISION": "digest-1",
            "EMBEDDING_DIMENSIONS": 768,
            "EMBEDDING_DOCUMENT_PREFIX": "search_document: ",
            "EMBEDDING_QUERY_PREFIX": "search_query: ",
            **changes,
        },
    )


def test_configuration_preserves_prefix_whitespace_and_changes_namespace():
    first = build_embedding_space(settings())
    assert first.document_prefix == "search_document: "
    assert first.query_prefix == "search_query: "
    assert first.dimensions == 768
    assert first.key != build_embedding_space(settings(EMBEDDING_MODEL_REVISION="digest-2")).key
    assert first.key != build_embedding_space(settings(EMBEDDING_DOCUMENT_PREFIX="")).key


@pytest.mark.parametrize(
    "changes",
    [
        {"EMBEDDING_MODEL": None},
        {"EMBEDDING_MODEL_REVISION": None},
        {"EMBEDDING_DIMENSIONS": None},
    ],
)
def test_missing_space_configuration_fails_explicitly(changes):
    with pytest.raises(ValueError, match="Configure embedding"):
        build_embedding_space(settings(**changes))
