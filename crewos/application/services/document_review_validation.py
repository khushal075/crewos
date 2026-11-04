"""Deterministic checks around a fallible semantic review and its recovery receipt."""

from hashlib import sha256
import json

from crewos.application.dtos.document_answer import AnsweredDocumentAnswer
from crewos.application.dtos.document_review import DocumentReview, DocumentReviewRequest
from crewos.application.dtos.source_reference import SourceReference


VALIDATION_POLICY_VERSION = "document_support_gate_v3"


class DocumentReviewError(RuntimeError):
    """A document answer was rejected, uncertain, or lacks valid review evidence."""


def source_key(passage):
    return SourceReference(
        document_id=passage.document_id,
        document_version=passage.document_version,
        chunk_index=passage.chunk.index,
    )


def request_digest(request: DocumentReviewRequest) -> str:
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def validate_document_review(request: DocumentReviewRequest, review: DocumentReview) -> None:
    if isinstance(request.answer, AnsweredDocumentAnswer):
        if (
            review.verdict != "supported"
            or review.evidence_answerability != "answerable"
            or not review.covers_all_claims
            or not review.addresses_question
            or review.evidence_conflict
            or not review.claims
        ):
            raise DocumentReviewError("Document answer did not pass evidence review")
        passages = {source_key(p): p for p in request.passages}
        covered_positions = set()
        for claim in review.claims:
            start = request.answer.answer.find(claim.claim)
            if start < 0:
                raise DocumentReviewError("Review claim must be an exact span of the answer")
            # Every occurrence is the same literal claim; punctuation/spacing alone
            # does not create an uncovered factual assertion.
            while start >= 0:
                covered_positions.update(range(start, start + len(claim.claim)))
                start = request.answer.answer.find(claim.claim, start + 1)
            if claim.verdict != "supported" or not claim.excerpts:
                raise DocumentReviewError("A claim lacks supported evidence")
            for excerpt in claim.excerpts:
                passage = passages.get(excerpt.source)
                if (
                    excerpt.source not in request.answer.citations
                    or passage is None
                    or excerpt.quote not in passage.chunk.text
                ):
                    raise DocumentReviewError("Review excerpt is not exact cited evidence")
        if any(
            char.isalnum() and index not in covered_positions
            for index, char in enumerate(request.answer.answer)
        ):
            raise DocumentReviewError("Review does not cover the complete answer text")
    elif (
        review.verdict != "appropriate_abstention"
        or review.evidence_answerability not in {"insufficient", "conflicting"}
        or review.claims
        or not review.addresses_question
    ):
        raise DocumentReviewError("Insufficient-evidence explanation did not pass review")


def review_receipt(request: DocumentReviewRequest, review: DocumentReview) -> dict:
    validate_document_review(request, review)
    return {
        "validation_policy_version": VALIDATION_POLICY_VERSION,
        "request_sha256": request_digest(request),
        "review": review.model_dump(mode="json"),
    }


def validate_review_receipt(request: DocumentReviewRequest, receipt: dict) -> None:
    if receipt.get("validation_policy_version") != VALIDATION_POLICY_VERSION or receipt.get(
        "request_sha256"
    ) != request_digest(request):
        raise DocumentReviewError(
            "Document review receipt does not match final answer and evidence"
        )
    validate_document_review(request, DocumentReview.model_validate(receipt.get("review")))
