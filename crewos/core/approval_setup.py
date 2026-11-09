"""Compose the opt-in demo action and its durable approval gate."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from crewos.application.services.action_approval import ActionApprovalGate
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyEnforcedExecutor
from crewos.application.services.tool_registry import ToolDefinition
from crewos.core.config import settings
from crewos.infrastructure.action_approval_store import SQLiteActionApprovalStore
from crewos.infrastructure.logging import get_logger


class IncidentNoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    incident_id: str = Field(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    note: str = Field(strict=True, min_length=1, max_length=1000)


class IncidentNoteWritten(BaseModel):
    note_id: UUID


def build_approval_components():
    store = SQLiteActionApprovalStore(
        settings.AGENT_ACTION_DB_PATH, ttl_seconds=settings.AGENT_ACTION_APPROVAL_TTL
    )
    logger = get_logger("crewos.approvals")
    gate = ActionApprovalGate(
        store=store,
        executor=PolicyEnforcedExecutor(PermissionPolicyService(), logger=logger),
        logger=logger,
    )

    def add_note(*, arguments: IncidentNoteInput, context):
        return IncidentNoteWritten(
            note_id=store.add_demo_note(
                tenant=context.tenant_id, incident_id=arguments.incident_id, note=arguments.note
            )
        )

    tool = ToolDefinition(
        name="add_demo_incident_note",
        description="Propose adding a note to the local demo incident log. Requires human approval.",
        argument_schema=IncidentNoteInput,
        handler=add_note,
        approval_permission="incident_note.write",
        approval_version="1",
    )
    return store, gate, tool
