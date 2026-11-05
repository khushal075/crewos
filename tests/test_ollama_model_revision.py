"""Digest matching without inference or network access."""

import pytest

from crewos.third_party.llm.ollama_model_revision import (
    ModelRevisionVerificationError,
    verify_ollama_model_revision,
)

DIGEST = "a" * 64


def entry(**changes):
    return {"name": "example:v1", "model": "example:v1", "digest": DIGEST, **changes}


def verify(metadata, **changes):
    return verify_ollama_model_revision(
        metadata=metadata,
        **{
            "model": "example:v1",
            "expected_digest": DIGEST,
            **changes,
        },
    )


def test_exact_tag_and_digest_match_with_unrelated_metadata():
    assert verify({"models": [entry(name="other:v1", model="other:v1"), entry(size=123)]}) == DIGEST


def test_model_alias_field_can_be_absent():
    assert verify({"models": [{"name": "example:v1", "digest": DIGEST}]}) == DIGEST


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        [],
        {},
        {"models": None},
        {"models": "invalid"},
        {"models": [{}]},
        {"models": [entry(digest="short")]},
        {"models": [entry(digest="A" * 64)]},
        {"models": [entry(digest="sha256:" + DIGEST)]},
        {"models": [entry(digest=123)]},
        {"models": [entry(name=123)]},
        {"models": [entry(model=123)]},
    ],
)
def test_malformed_metadata_fails_closed(metadata):
    with pytest.raises(ModelRevisionVerificationError):
        verify(metadata)


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [entry(name="example:latest", model="example:latest")],
        [entry(), entry()],
    ],
)
def test_missing_implicit_alias_or_ambiguous_tag_is_rejected(entries):
    with pytest.raises(ModelRevisionVerificationError, match="one exact"):
        verify({"models": entries})


def test_conflicting_alias_is_rejected():
    with pytest.raises(ModelRevisionVerificationError, match="aliases disagree"):
        verify({"models": [entry(model="different:v1")]})


def test_different_digest_is_rejected_even_when_tag_matches():
    with pytest.raises(ModelRevisionVerificationError, match="differs"):
        verify({"models": [entry(digest="b" * 64)]})


@pytest.mark.parametrize("expected", ["", "a" * 12, "A" * 64, "sha256:" + DIGEST])
def test_expected_digest_requires_full_lowercase_sha256(expected):
    with pytest.raises(ModelRevisionVerificationError):
        verify({"models": [entry()]}, expected_digest=expected)


def test_error_does_not_include_metadata_content():
    with pytest.raises(ModelRevisionVerificationError) as captured:
        verify({"models": [{"private": "private_marker"}]})
    assert "private_marker" not in str(captured.value)
    assert captured.value.__suppress_context__
