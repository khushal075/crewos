"""Check citation provenance, independently of whether evidence supports a claim."""

from collections.abc import Iterable

from crewos.application.dtos.model_decision import FinalAnswerDecision
from crewos.application.dtos.document_answer import AnsweredDocumentAnswer
from crewos.application.dtos.source_reference import SourceReference


class UnobservedCitationError(RuntimeError):
    """The answer cites a passage absent from this run's trusted search observations."""


def validate_answer_citations(
    answer: FinalAnswerDecision | AnsweredDocumentAnswer, *, observed: Iterable[SourceReference]
) -> None:
    available = set(observed)
    if any(reference not in available for reference in answer.citations):
        raise UnobservedCitationError("Answer cites a source not observed by this run")
