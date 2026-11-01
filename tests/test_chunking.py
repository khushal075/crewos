"""Public chunking behavior, source integrity, limits, and module boundaries."""

import ast
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from crewos.chunking import (
    ChunkingLimitExceeded,
    ChunkingOptions,
    ChunkingRequest,
    ChunkingResult,
    ChunkingService,
    TextChunk,
    build_character_chunking,
)
from crewos.ingestion import build_plain_text_preparation


def request(text, size=10, overlap=2):
    return ChunkingRequest(text=text, options=ChunkingOptions(chunk_size=size, overlap=overlap))


def test_prepared_document_splits_through_public_module_boundaries():
    document = build_plain_text_preparation().prepare(
        content=b"Check logs. Retry later.", title="CRM recovery"
    )
    result = build_character_chunking().chunk(request(document.text))
    assert result.strategy == "character_window_v1"
    assert [(c.start_char, c.end_char) for c in result.chunks] == [(0, 10), (8, 18), (16, 24)]
    assert [c.text for c in result.chunks] == [
        document.text[0:10],
        document.text[8:18],
        document.text[16:24],
    ]
    assert ChunkingResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("length", [1, 9, 10, 11, 18, 19, 20, 26])
def test_short_exact_and_partial_windows_cover_every_source_character(length):
    text = ("é🙂\nabc " * 5)[:length]
    result = build_character_chunking().chunk(request(text))
    recovered = result.chunks[0].text
    for previous, current in zip(result.chunks, result.chunks[1:]):
        assert current.start_char == previous.end_char - 2
        recovered += current.text[previous.end_char - current.start_char :]
    assert recovered == text
    assert result.chunks[-1].end_char == len(text)


def test_zero_overlap_can_reconstruct_source_by_joining():
    text = "Runbook\n\n  retry()\n"
    result = build_character_chunking().chunk(request(text, size=4, overlap=0))
    assert "".join(chunk.text for chunk in result.chunks) == text
    assert all(len(chunk.text) <= 4 for chunk in result.chunks)


def test_many_window_settings_preserve_source_spans_and_contiguous_indices():
    text = "Aé🙂\n  retry webhook!" * 3
    for size in range(1, 12):
        for overlap in range(size):
            result = build_character_chunking().chunk(request(text, size, overlap))
            assert [c.index for c in result.chunks] == list(range(len(result.chunks)))
            assert result.chunks[0].start_char == 0
            assert result.chunks[-1].end_char == len(text)
            for chunk in result.chunks:
                assert chunk.text == text[chunk.start_char : chunk.end_char]
                assert 0 < len(chunk.text) <= size


@pytest.mark.parametrize(
    "options",
    [
        {"chunk_size": 0},
        {"chunk_size": True},
        {"chunk_size": 16385},
        {"chunk_size": "1000"},
        {"overlap": -1},
        {"overlap": True},
        {"chunk_size": 10, "overlap": 10},
        {"chunk_size": 10, "overlap": 11},
    ],
)
def test_rejects_invalid_window_configuration(options):
    with pytest.raises(ValidationError):
        ChunkingOptions(**options)


@pytest.mark.parametrize("text", ["", " \n", 123, "x" * 1_000_001])
def test_rejects_invalid_document_text(text):
    with pytest.raises(ValidationError):
        ChunkingRequest(text=text)


def test_rejects_tenant_and_permission_claims_in_chunking_payload():
    with pytest.raises(ValidationError):
        ChunkingRequest(text="Details", tenant_id="tenant_b", permissions=["write"])


@pytest.mark.parametrize("limits", [{"max_chunks": 1}, {"max_output_chars": 22}])
def test_checks_output_expansion_before_allocating_chunks(limits):
    with patch("crewos.chunking.strategies.character_window.TextChunk") as constructor:
        with pytest.raises(ChunkingLimitExceeded):
            build_character_chunking(**limits).chunk(request("x" * 23))
        constructor.assert_not_called()


def test_output_limits_accept_exact_boundary():
    result = build_character_chunking(max_chunks=3, max_output_chars=27).chunk(request("x" * 23))
    assert len(result.chunks) == 3
    assert sum(len(c.text) for c in result.chunks) == 27


@pytest.mark.parametrize("limits", [{"max_chunks": 0}, {"max_output_chars": True}])
def test_rejects_invalid_server_limits(limits):
    with pytest.raises(ValueError):
        build_character_chunking(**limits)


class FixtureStrategy:
    name = "fixture_v1"

    def __init__(self, chunks):
        self.chunks = chunks

    def split(self, request, *, max_chunks, max_output_chars):
        return self.chunks


def test_can_inject_another_strategy():
    chunk = TextChunk(index=0, text="abc", start_char=0, end_char=3)
    result = ChunkingService(FixtureStrategy((chunk,))).chunk(request("abc"))
    assert result.strategy == "fixture_v1"
    assert result.chunks == (chunk,)


@pytest.mark.parametrize(
    "chunks",
    [
        (),
        (TextChunk(index=0, text="wrong", start_char=0, end_char=5),),
        (TextChunk(index=0, text="ab", start_char=0, end_char=2),),
        (TextChunk(index=1, text="abc", start_char=0, end_char=3),),
        (TextChunk(index=0, text="bc", start_char=1, end_char=3),),
        (
            TextChunk(index=0, text="a", start_char=0, end_char=1),
            TextChunk(index=1, text="c", start_char=2, end_char=3),
        ),
    ],
)
def test_rejects_incomplete_or_inconsistent_strategy_output(chunks):
    with pytest.raises(ValueError):
        ChunkingService(FixtureStrategy(chunks)).chunk(request("abc"))


def test_rechecks_limits_on_injected_strategy_output():
    chunks = (
        TextChunk(index=0, text="ab", start_char=0, end_char=2),
        TextChunk(index=1, text="bc", start_char=1, end_char=3),
    )
    with pytest.raises(ChunkingLimitExceeded):
        ChunkingService(FixtureStrategy(chunks), max_output_chars=3).chunk(request("abc"))


def test_ingestion_chunk_import_remains_compatible():
    from crewos.ingestion import TextChunk as ExistingChunk

    assert ExistingChunk is TextChunk


def test_chunking_is_independent_and_callers_use_its_public_boundary():
    root = Path(__file__).resolve().parents[1] / "crewos"
    violations = []
    for path in root.rglob("*.py"):
        inside = path.is_relative_to(root / "chunking")
        for node in ast.walk(ast.parse(path.read_text())):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            )
            for module in modules:
                if (
                    inside
                    and module.startswith("crewos.")
                    and not module.startswith("crewos.chunking.")
                ):
                    violations.append(f"{path.name}: external platform dependency {module}")
                if not inside and module.startswith("crewos.chunking."):
                    violations.append(f"{path.name}: private chunking import {module}")
    assert not violations, "\n".join(violations)
