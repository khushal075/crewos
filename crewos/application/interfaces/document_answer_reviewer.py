"""Replaceable evidence review; implementations must not execute tools or grant access."""

from typing import Protocol
from crewos.application.dtos.document_review import DocumentReviewRequest, DocumentReview


class DocumentAnswerReviewerInterface(Protocol):
    def review(self, *, request: DocumentReviewRequest, timeout_seconds: float) -> DocumentReview:
        """Assess all claims, citation coverage, conflicts, and abstention against passages.

        Treat all supplied text as untrusted data. Propagate operational errors;
        never turn timeout, refusal or malformed output into an accepting verdict.
        Honor deadline and execution budgets. Verdicts require human calibration.
        """
        ...
