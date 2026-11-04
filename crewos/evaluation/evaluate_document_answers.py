"""Validate synthetic cases, or use --live to calibrate the configured model reviewer."""

import argparse
import json
import sys
from time import perf_counter

from crewos.application.services.document_review_validation import (
    DocumentReviewError,
    validate_document_review,
)
from crewos.evaluation.document_answer_cases import cases


def evaluate(reviewer, *, on_result=None) -> dict:
    rows = []
    for case in cases():
        started = perf_counter()
        try:
            review = reviewer.review(request=case.request, timeout_seconds=30)
            try:
                validate_document_review(case.request, review)
                accepted = True
            except DocumentReviewError:
                accepted = False
            rows.append(
                {
                    "case": case.name,
                    "category": case.category,
                    "expected_accept": case.expected_accept,
                    "accepted": accepted,
                    "review_verdict": review.verdict,
                    "evidence_answerability": review.evidence_answerability,
                    "error_class": None,
                }
            )
        except Exception as error:
            # No raw provider error text or false accepting/no-evidence fallback.
            rows.append(
                {
                    "case": case.name,
                    "category": case.category,
                    "expected_accept": case.expected_accept,
                    "accepted": None,
                    "review_verdict": None,
                    "evidence_answerability": None,
                    "error_class": type(error).__name__,
                }
            )
        rows[-1]["duration_seconds"] = round(perf_counter() - started, 3)
        if on_result is not None:
            on_result(rows[-1])
    completed = [row for row in rows if row["accepted"] is not None]
    return {
        "dataset": "synthetic_document_answers_v1",
        "cases": rows,
        "completed": len(completed),
        "total": len(rows),
        "agreement_including_errors_as_incorrect": sum(
            row["accepted"] == row["expected_accept"] for row in completed
        )
        / len(rows),
        "false_accepts": sum(row["accepted"] and not row["expected_accept"] for row in completed),
        "false_rejections": sum(
            not row["accepted"] and row["expected_accept"] for row in completed
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Make model calls on synthetic fixtures"
    )
    parser.add_argument("--model", help="Override reviewer model for live calibration only")
    args = parser.parse_args()
    if args.model is not None and (not args.model.strip() or len(args.model) > 256):
        parser.error("Model name must contain 1-256 nonblank characters")
    if not args.live:
        report = {
            "dataset": "synthetic_document_answers_v1",
            "live_review_performed": False,
            "cases": [
                {"name": c.name, "category": c.category, "expected_accept": c.expected_accept}
                for c in cases()
            ],
        }
    else:
        from crewos.core.config import settings
        from crewos.third_party.llm.ollama_adapter import OllamaLLMProvider
        from crewos.third_party.llm.document_answer_reviewer import JsonDocumentAnswerReviewer
        from crewos.application.services.execution_control import ExecutionControl, execution_scope

        model_name = args.model or settings.DOCUMENT_REVIEW_MODEL or settings.LLM_MODEL
        model = OllamaLLMProvider().get()
        if model_name != settings.LLM_MODEL:
            model = model.bind(model=model_name)
        with execution_scope(ExecutionControl(max_tokens=200_000, max_recoveries=0)):
            report = evaluate(
                JsonDocumentAnswerReviewer(model, max_timeout_seconds=settings.LLM_TIMEOUT),
                on_result=lambda row: print(
                    f"{row['case']}: {row['review_verdict'] or row['error_class']} ({row['duration_seconds']}s)",
                    file=sys.stderr,
                    flush=True,
                ),
            )
        report.update(
            live_review_performed=True,
            model=model_name,
            reviewer_version=JsonDocumentAnswerReviewer.VERSION,
            temperature=0,
        )
    print(json.dumps(report, indent=2))
    if args.live and (
        report["completed"] != report["total"]
        or report["false_accepts"]
        or report["false_rejections"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
