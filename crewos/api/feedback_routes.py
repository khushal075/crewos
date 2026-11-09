"""Tenant-scoped admission of curated feedback for completed agent runs."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException

from crewos.api.agent_routes import get_agent_submission_executor, run_operation
from crewos.core.config import settings
from crewos.core.tenant import get_tool_execution_context, require_reviewer_token
from crewos.evaluation.feedback_cases import (
    FeedbackConflict,
    FeedbackExpectation,
    SQLiteFeedbackStore,
)
from crewos.infrastructure.run_store import get_run_store

router = APIRouter()


@router.post("/agents/runs/{run_id}/feedback", status_code=201)
def submit_feedback(
    run_id: UUID,
    expectation: FeedbackExpectation,
    x_approval_token: str | None = Header(default=None, max_length=4096),
    context=Depends(get_tool_execution_context),
    run_store=Depends(get_run_store),
):
    def add():
        if not settings.AGENT_FEEDBACK_ENABLED:
            raise HTTPException(404, "Agent feedback unavailable")
        require_reviewer_token(context.tenant_id, x_approval_token)
        record = run_store.get(str(run_id), context.tenant_id)
        if record["state"] != "succeeded" or record["result"] is None:
            raise HTTPException(409, "Only completed answers can become feedback cases")
        store = SQLiteFeedbackStore(settings.AGENT_FEEDBACK_DB_PATH)
        try:
            store.add(
                tenant=context.tenant_id,
                run_id=str(run_id),
                expectation=expectation,
                baseline=record["result"],
            )
        except FeedbackConflict:
            raise HTTPException(409, "Feedback conflicts with the completed run") from None
        return {"run_id": str(run_id), "status": "recorded"}

    return run_operation(get_agent_submission_executor(), context, "agent_feedback.review", add)
