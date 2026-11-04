"""Synthetic in-process smoke: real configured generation/review, temporary storage."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import Mock, patch
from uuid import UUID

from crewos.application.dtos.decision_request import DecisionMessage
from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.execution_control import ExecutionControl
from crewos.chunking import ChunkingRequest, ChunkingOptions, build_character_chunking
from crewos.core.agent_setup import build_agent_loop
from crewos.core.config import settings
from crewos.ingestion import DocumentInput, StoredDocumentVersion, build_sqlite_document_store
from crewos.third_party.llm.ollama_adapter import OllamaLLMProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/crewos-live-workflow-smoke.json"))
    args = parser.parse_args()
    rows = []
    with TemporaryDirectory(prefix="crewos-live-workflow-") as directory:
        store = build_sqlite_document_store(Path(directory) / "documents.sqlite3")
        text = "For timeout failures, retry after 30 seconds. Do not retry HTTP 401 errors."
        store.create_version(
            StoredDocumentVersion(
                tenant_id="synthetic_demo",
                document_id=UUID(int=11, version=4),
                version=1,
                document=DocumentInput(
                    title="Synthetic timeout runbook", text=text, source_ref="synthetic:timeout"
                ),
                chunking=build_character_chunking().chunk(
                    ChunkingRequest(text=text, options=ChunkingOptions(chunk_size=1000, overlap=0))
                ),
            )
        )
        for name, question, expected in (
            (
                "answerable",
                "Use search_documents to find how long we should wait before retrying timeout failures.",
                "answered",
            ),
            (
                "missing_evidence",
                "Use search_documents to find the certificate rotation interval.",
                "insufficient_evidence",
            ),
        ):
            control = ExecutionControl()
            started = perf_counter()
            try:
                with patch("crewos.core.retrieval_setup.get_document_store", return_value=store):
                    answer = build_agent_loop(
                        model=OllamaLLMProvider().get(),
                        reader=Mock(),
                        answer_mode="document",
                        max_decisions=5,
                    ).run(
                        messages=[DecisionMessage(role="user", content=question)],
                        context=ToolExecutionContext(
                            tenant_id="synthetic_demo",
                            permissions=frozenset({"document.search", "document.read"}),
                        ),
                        control=control,
                    )
                rows.append(
                    {
                        "case": name,
                        "expected_outcome": expected,
                        "result": answer.model_dump(mode="json"),
                        "review_verdict": control.state["document_review"]["review"]["verdict"],
                        "passed": answer.result.outcome == expected,
                        "error_class": None,
                        "duration_seconds": round(perf_counter() - started, 3),
                    }
                )
            except Exception as error:
                rows.append(
                    {
                        "case": name,
                        "expected_outcome": expected,
                        "result": None,
                        "passed": False,
                        "error_class": type(error).__name__,
                        "duration_seconds": round(perf_counter() - started, 3),
                    }
                )
            print(
                {
                    "smoke_case": name,
                    "passed": rows[-1]["passed"],
                    "error_class": rows[-1]["error_class"],
                },
                flush=True,
            )
    report = {
        "model": settings.LLM_MODEL,
        "reviewer_model": settings.DOCUMENT_REVIEW_MODEL or settings.LLM_MODEL,
        "context_source": "synthetic trusted fixture; authentication not exercised",
        "storage": "temporary SQLite; no tenant application documents used",
        "cases": rows,
    }
    args.output.write_text(json.dumps(report, indent=2))
    if not all(row["passed"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
