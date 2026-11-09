"""Turn curated run feedback into deterministic, comparable regression cases."""

import json
import sqlite3
from contextlib import closing
from time import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from crewos.application.dtos.document_answer import AnsweredDocumentAnswer
from crewos.application.dtos.model_decision import AgentAnswer, DocumentFinalAnswerDecision
from crewos.application.dtos.source_reference import SourceReference

_ANSWER: TypeAdapter[AgentAnswer] = TypeAdapter(AgentAnswer)
_Phrase = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)
]


class FeedbackExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_outcome: Literal["final_answer", "answered", "insufficient_evidence"]
    required_phrases: tuple[_Phrase, ...] = Field(default=(), max_length=12)
    forbidden_phrases: tuple[_Phrase, ...] = Field(default=(), max_length=12)
    required_citations: tuple[SourceReference, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def check_criteria(self):
        required = {value.casefold() for value in self.required_phrases}
        forbidden = {value.casefold() for value in self.forbidden_phrases}
        if required & forbidden:
            raise ValueError("A phrase cannot be both required and forbidden")
        if self.expected_outcome == "insufficient_evidence":
            if self.required_citations:
                raise ValueError("An abstention cannot require citations")
        elif not (required or forbidden or self.required_citations):
            raise ValueError("An answer expectation needs at least one content criterion")
        return self


class FeedbackConflict(RuntimeError):
    pass


class SQLiteFeedbackStore:
    def __init__(self, path):
        self.path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS agent_feedback ("
                "tenant TEXT NOT NULL, run_id TEXT NOT NULL, expectation TEXT NOT NULL, "
                "baseline TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY (tenant, run_id))"
            )

    def add(self, *, tenant, run_id, expectation: FeedbackExpectation, baseline):
        answer = _ANSWER.validate_python(baseline)
        if isinstance(answer, DocumentFinalAnswerDecision) == (
            expectation.expected_outcome == "final_answer"
        ):
            raise FeedbackConflict("Expected outcome must match the run's answer mode")
        baseline_json = answer.model_dump_json()
        if len(baseline_json.encode("utf-8")) > 65536:
            raise FeedbackConflict("Baseline exceeds feedback case size limit")
        try:
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute(
                    "INSERT INTO agent_feedback VALUES (?, ?, ?, ?, ?)",
                    (
                        tenant,
                        run_id,
                        expectation.model_dump_json(),
                        baseline_json,
                        time(),
                    ),
                )
        except sqlite3.IntegrityError:
            raise FeedbackConflict("A feedback case already exists for this run") from None

    def cases(self, *, tenant, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Feedback evaluation limit must be within 1–1000")
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute(
                "SELECT run_id, expectation, baseline FROM agent_feedback "
                "WHERE tenant = ? ORDER BY created, run_id LIMIT ?",
                (tenant, limit),
            ).fetchall()


def score(expectation: FeedbackExpectation, candidate) -> dict:
    if candidate is None:
        return {"passed": False, "failures": ["missing_candidate"]}
    try:
        answer = _ANSWER.validate_python(candidate)
    except ValueError:
        return {"passed": False, "failures": ["invalid_candidate"]}
    outcome: str
    citations: tuple[SourceReference, ...]
    if isinstance(answer, DocumentFinalAnswerDecision):
        result = answer.result
        outcome = result.outcome.value
        if isinstance(result, AnsweredDocumentAnswer):
            text, citations = result.answer, result.citations
        else:
            text, citations = result.explanation, ()
    else:
        outcome, text, citations = "final_answer", answer.answer, answer.citations
    normalized = (text or "").casefold()
    failures = []
    if outcome != expectation.expected_outcome:
        failures.append("outcome")
    if any(phrase.casefold() not in normalized for phrase in expectation.required_phrases):
        failures.append("missing_required_phrase")
    if any(phrase.casefold() in normalized for phrase in expectation.forbidden_phrases):
        failures.append("contains_forbidden_phrase")
    if not set(expectation.required_citations).issubset(citations):
        failures.append("missing_required_citation")
    return {"passed": not failures, "failures": failures}


def evaluate_feedback(cases, candidates: dict | None = None) -> dict:
    rows = []
    for run_id, expected, baseline in cases:
        expectation = FeedbackExpectation.model_validate_json(expected)
        row = {"run_id": run_id, "baseline": score(expectation, json.loads(baseline))}
        if candidates is not None:
            row["candidate"] = score(expectation, candidates.get(run_id))
        rows.append(row)
    report = {
        "total": len(rows),
        "baseline_passed": sum(row["baseline"]["passed"] for row in rows),
        "rows": rows,
    }
    if candidates is not None:
        report.update(
            candidate_passed=sum(row["candidate"]["passed"] for row in rows),
            regressions=sum(
                row["baseline"]["passed"] and not row["candidate"]["passed"] for row in rows
            ),
            improvements=sum(
                not row["baseline"]["passed"] and row["candidate"]["passed"] for row in rows
            ),
        )
    return report
