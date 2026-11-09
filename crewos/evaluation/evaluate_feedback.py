"""Compare reviewed agent runs with candidate outputs offline."""

import argparse
import json
from pathlib import Path

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.core.config import settings
from crewos.evaluation.feedback_cases import SQLiteFeedbackStore, evaluate_feedback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--candidates", type=Path, help="Private JSON object mapping run IDs to answers"
    )
    args = parser.parse_args()
    tenant = ToolExecutionContext(tenant_id=args.tenant_id).tenant_id
    candidates = None
    if args.candidates:
        with args.candidates.open("rb") as stream:
            data = stream.read(8_000_001)
        if len(data) > 8_000_000:
            parser.error("Candidate file exceeds 8 MB")
        candidates = json.loads(data)
        if not isinstance(candidates, dict):
            parser.error("Candidates must be a JSON object keyed by run ID")
    store = SQLiteFeedbackStore(settings.AGENT_FEEDBACK_DB_PATH)
    report = evaluate_feedback(store.cases(tenant=tenant, limit=args.limit), candidates)
    print(json.dumps(report, indent=2))
    return (
        1
        if not report["total"]
        or (candidates is not None and report["candidate_passed"] != report["total"])
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
