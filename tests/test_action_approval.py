from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

from crewos.application.dtos.tool_execution_context import ToolExecutionContext
from crewos.application.services.action_approval import (
    ActionApprovalGate,
    ApprovalConflict,
    ApprovalRequested,
    ApprovalUnavailable,
)
from crewos.application.services.permission_policy import PermissionPolicyService
from crewos.application.services.policy_executor import PolicyDeniedError, PolicyEnforcedExecutor
from crewos.application.services.tool_dispatcher import ToolDispatcher
from crewos.application.services.tool_registry import ToolDefinition, ToolRegistry
from crewos.core.approval_setup import IncidentNoteInput, IncidentNoteWritten
from crewos.infrastructure.action_approval_store import SQLiteActionApprovalStore


@pytest.fixture
def system(tmp_path):
    now = [100.0]
    store = SQLiteActionApprovalStore(tmp_path / "actions.db", ttl_seconds=60, clock=lambda: now[0])
    writes = []

    def handler(*, arguments, context):
        writes.append((arguments.note, context.tenant_id))
        return IncidentNoteWritten(note_id=uuid4())

    tool = ToolDefinition(
        "note",
        "approved note",
        IncidentNoteInput,
        handler,
        approval_permission="incident_note.write",
        approval_version="v1",
    )
    registry = ToolRegistry()
    registry.register(tool)
    gate = ActionApprovalGate(
        store=store, executor=PolicyEnforcedExecutor(PermissionPolicyService())
    )
    dispatcher = ToolDispatcher(registry, approval_gate=gate)
    ctx = ToolExecutionContext(tenant_id="tenant_a", permissions={"incident_note.write"})

    def propose(context=ctx):
        result = dispatcher.dispatch(
            name="note",
            arguments={"incident_id": "id1", "note": "approved exact note"},
            context=context,
        )
        assert isinstance(result, ApprovalRequested)
        return str(result.approval_id)

    def execute(action, context=ctx, resolved=tool):
        return gate.execute(approval_id=action, context=context, resolve=lambda _: resolved)

    return store, writes, now, ctx, tool, propose, execute


def test_no_side_effect_without_approval_and_concurrent_claim_is_one_shot(system):
    store, writes, _, _, _, propose, execute = system
    action = propose()
    with pytest.raises(ApprovalConflict):
        execute(action)
    assert writes == []
    store.review(action, "tenant_a", approve=True)

    def attempt():
        try:
            execute(action)
            return True
        except ApprovalConflict:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == [False, True]
    assert writes == [("approved exact note", "tenant_a")]
    assert store.get(action, "tenant_a")["state"] == "completed"


def test_scope_expiry_revocation_and_contract_version_block_execution(system):
    store, writes, now, _, tool, propose, execute = system
    no_permission = ToolExecutionContext(tenant_id="tenant_a")
    with pytest.raises(PolicyDeniedError):
        propose(no_permission)
    action = propose()
    with pytest.raises(ApprovalUnavailable):
        store.review(action, "tenant_b", approve=True)
    store.review(action, "tenant_a", approve=True)
    with pytest.raises(PolicyDeniedError):
        execute(action, no_permission)
    with pytest.raises(ApprovalConflict):
        execute(action, resolved=replace(tool, approval_version="v2"))
    now[0] = 161
    assert store.public(store.get(action, "tenant_a"))["state"] == "expired"
    with pytest.raises(ApprovalConflict):
        execute(action)
    assert writes == []


def test_rejection_and_uncertain_action_cannot_be_replayed(system):
    store, writes, _, _, tool, propose, execute = system
    rejected = propose()
    store.review(rejected, "tenant_a", approve=False)
    with pytest.raises(ApprovalConflict):
        store.review(rejected, "tenant_a", approve=True)
    with pytest.raises(ApprovalConflict):
        execute(rejected)

    action = propose()
    store.review(action, "tenant_a", approve=True)

    def ambiguous(**kwargs):
        tool.handler(**kwargs)
        raise TimeoutError("Response lost")

    with pytest.raises(TimeoutError):
        execute(action, resolved=replace(tool, handler=ambiguous))
    assert store.get(action, "tenant_a")["state"] == "uncertain"
    with pytest.raises(ApprovalConflict):
        execute(action)
    assert len(writes) == 1


def test_mutating_tool_fails_closed_without_gate(system):
    _, writes, _, ctx, tool, _, _ = system
    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.register(replace(tool, replay_safe=True))
    registry.register(tool)
    with pytest.raises(RuntimeError, match="Approval gate"):
        ToolDispatcher(registry).dispatch(
            name="note", arguments={"incident_id": "id1", "note": "text"}, context=ctx
        )
    assert writes == []
