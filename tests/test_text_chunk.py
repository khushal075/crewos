"""Chunk spans use Python character offsets and retain exact source formatting."""

import pytest
from pydantic import ValidationError

from crewos.application.dtos.text_chunk import TextChunk


def test_chunk_uses_zero_based_index_and_exclusive_end():
    source = "Check logs. Retry later."
    chunk = TextChunk(index=0, text=source[0:11], start_char=0, end_char=11)
    assert chunk.text == "Check logs."
    assert source[chunk.start_char : chunk.end_char] == chunk.text
    assert TextChunk.model_validate_json(chunk.model_dump_json()) == chunk


def test_offsets_count_unicode_characters_not_utf8_bytes():
    source = "Aé🙂Z"
    chunk = TextChunk(index=1, text=source[1:3], start_char=1, end_char=3)
    assert chunk.text == "é🙂"
    assert len(chunk.text) == 2
    assert len(chunk.text.encode("utf-8")) == 6


def test_preserves_whitespace_including_whitespace_only_segments():
    chunk = TextChunk(index=0, text=" \n", start_char=3, end_char=5)
    assert chunk.text == " \n"


@pytest.mark.parametrize(
    "changes",
    [
        {"index": -1},
        {"index": True},
        {"index": "0"},
        {"text": ""},
        {"text": 123},
        {"text": "too long"},
        {"text": "x" * 1_000_001},
        {"start_char": -1},
        {"start_char": True},
        {"start_char": 1_000_000},
        {"end_char": 0},
        {"end_char": 1.0},
        {"end_char": 1_000_001},
        {"start_char": 3, "end_char": 2},
        {"tenant_id": "tenant_b"},
    ],
)
def test_rejects_invalid_fields_and_inconsistent_spans(changes):
    with pytest.raises(ValidationError):
        TextChunk.model_validate(
            {"index": 0, "text": "abc", "start_char": 0, "end_char": 3, **changes}
        )


def test_chunk_is_immutable():
    chunk = TextChunk(index=0, text="abc", start_char=0, end_char=3)
    with pytest.raises(ValidationError):
        chunk.start_char = 1
