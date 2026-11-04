"""Backend rules for the explicitly selected document-answer workflow."""

from collections.abc import Iterable

from crewos.application.dtos.document_answer import AnsweredDocumentAnswer
from crewos.application.dtos.model_decision import DocumentFinalAnswerDecision
from crewos.application.dtos.source_reference import SourceReference
from crewos.application.services.citation_validation import validate_answer_citations


class DocumentAnswerError(RuntimeError):
    """A document-answer proposal did not follow the required evidence workflow."""


def validate_document_answer(
    answer: DocumentFinalAnswerDecision,
    *,
    observed: Iterable[SourceReference],
    search_completed: bool,
) -> None:
    if not search_completed:
        raise DocumentAnswerError("A successful document search is required before answering")
    if isinstance(answer.result, AnsweredDocumentAnswer):
        validate_answer_citations(answer.result, observed=observed)
