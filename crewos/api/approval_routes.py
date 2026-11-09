"""Human review and explicit execution of previously proposed actions."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool

from crewos.api.agent_routes import get_agent_submission_executor, run_operation
from crewos.application.services.action_approval import ApprovalConflict, ApprovalUnavailable
from crewos.core.approval_setup import build_approval_components
from crewos.core.config import settings
from crewos.core.tenant import get_tool_execution_context, require_reviewer_token
from crewos.infrastructure.logging import get_logger

router = APIRouter(prefix="/agents/actions")
logger = get_logger("crewos.approvals.api")


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: StrictBool


def _components():
    if not settings.AGENT_ACTIONS_ENABLED:
        raise HTTPException(404, "Agent actions unavailable")
    return build_approval_components()


def _call(context, permission, operation):
    def translate():
        try:
            return operation()
        except ApprovalUnavailable:
            raise HTTPException(404, "Approval unavailable") from None
        except ApprovalConflict:
            raise HTTPException(409, "Approval cannot perform this transition") from None

    return run_operation(get_agent_submission_executor(), context, permission, translate)


@router.get("")
def list_actions(run_id: UUID, context=Depends(get_tool_execution_context)):
    return _call(
        context,
        "agent_action.read",
        lambda: _components()[0].list_run(context.tenant_id, str(run_id)),
    )


@router.get("/{approval_id}")
def read_action(approval_id: UUID, context=Depends(get_tool_execution_context)):
    def operation():
        store, _, _ = _components()
        return store.public(store.get(str(approval_id), context.tenant_id))

    return _call(context, "agent_action.read", operation)


@router.post("/{approval_id}/review")
def review_action(
    approval_id: UUID,
    decision: ReviewDecision,
    x_approval_token: str | None = Header(default=None, max_length=4096),
    context=Depends(get_tool_execution_context),
):
    def operation():
        require_reviewer_token(context.tenant_id, x_approval_token)
        store, _, _ = _components()
        row = store.review(str(approval_id), context.tenant_id, approve=decision.approve)
        logger.info(
            {
                "event": "agent_action_reviewed",
                "approval_id": str(approval_id),
                "tenant_id": context.tenant_id,
                "state": row["state"],
            }
        )
        return store.public(row)

    return _call(context, "agent_action.review", operation)


@router.post("/{approval_id}/execute")
def execute_action(approval_id: UUID, context=Depends(get_tool_execution_context)):
    def operation():
        _, gate, tool = _components()

        def resolve(name):
            if name != tool.name:
                raise ApprovalConflict("Approved tool is unavailable")
            return tool

        return gate.execute(approval_id=str(approval_id), context=context, resolve=resolve)

    return _call(context, "agent_action.execute", operation)
