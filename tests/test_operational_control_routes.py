"""Verify tenant authentication, review credentials and feedback admission."""

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from crewos.application.interfaces.run_store import RunNotFound
from crewos.core.approval_setup import build_approval_components
from crewos.core.config import settings
from crewos.infrastructure.run_store import get_run_store
from crewos.main import app


def auth(tenant="tenant_a", *, review=False):
    headers = {"X-Tenant-Id": tenant, "Authorization": f"Bearer {tenant}-key"}
    if review:
        headers["X-Approval-Token"] = f"{tenant}-review"
    return headers


def configure(tmp_path, monkeypatch):
    for name, value in {
        "AGENT_ACTIONS_ENABLED": True,
        "AGENT_ACTION_DB_PATH": str(tmp_path / "actions.db"),
        "AGENT_FEEDBACK_ENABLED": True,
        "AGENT_FEEDBACK_DB_PATH": str(tmp_path / "feedback.db"),
        "TENANT_API_KEYS": {t: SecretStr(f"{t}-key") for t in ("tenant_a", "tenant_b")},
        "AGENT_REVIEWER_KEYS": {t: SecretStr(f"{t}-review") for t in ("tenant_a", "tenant_b")},
        "TENANT_PERMISSIONS": {
            t: frozenset(
                {
                    "agent_action.read",
                    "agent_action.review",
                    "agent_action.execute",
                    "incident_note.write",
                    "agent_feedback.review",
                }
            )
            for t in ("tenant_a", "tenant_b")
        },
    }.items():
        monkeypatch.setattr(settings, name, value)
    return TestClient(app)


def test_action_review_requires_distinct_credential_and_is_tenant_scoped(tmp_path, monkeypatch):
    client = configure(tmp_path, monkeypatch)
    store, _, tool = build_approval_components()
    run_id = str(uuid4())
    action_id = store.propose(
        tenant="tenant_a",
        tool=tool.name,
        version=tool.approval_version,
        payload='{"incident_id":"demo","note":"approved note"}',
        run_id=run_id,
    )
    path = f"/agents/actions/{action_id}"
    assert client.get(path, headers=auth("tenant_b")).status_code == 404
    assert (
        client.post(
            path + "/review", headers=auth("tenant_b", review=True), json={"approve": True}
        ).status_code
        == 404
    )
    assert (
        client.get(f"/agents/actions?run_id={run_id}", headers=auth()).json()[0]["approval_id"]
        == action_id
    )
    assert client.post(path + "/review", headers=auth(), json={"approve": True}).status_code == 403
    monkeypatch.setitem(settings.AGENT_REVIEWER_KEYS, "tenant_a", SecretStr("tenant_a-key"))
    assert (
        client.post(
            path + "/review",
            headers={**auth(), "X-Approval-Token": "tenant_a-key"},
            json={"approve": True},
        ).status_code
        == 403
    )
    monkeypatch.setitem(settings.AGENT_REVIEWER_KEYS, "tenant_a", SecretStr("tenant_a-review"))
    assert (
        client.post(path + "/review", headers=auth(review=True), json={"approve": False}).json()[
            "state"
        ]
        == "rejected"
    )
    assert client.post(path + "/execute", headers=auth()).status_code == 409


def test_feedback_is_reviewed_completed_tenant_owned_and_immutable(tmp_path, monkeypatch):
    client = configure(tmp_path, monkeypatch)
    state = "running"
    run_id = str(uuid4())

    def get(given_id, tenant):
        if given_id != run_id or tenant != "tenant_a":
            raise RunNotFound("unavailable")
        return {"state": state, "result": {"type": "final_answer", "answer": "original"}}

    monkeypatch.setitem(app.dependency_overrides, get_run_store, lambda: SimpleNamespace(get=get))
    path = f"/agents/runs/{run_id}/feedback"
    criteria = {"expected_outcome": "final_answer", "required_phrases": ["original"]}
    assert client.post(path, headers=auth(), json=criteria).status_code == 403
    assert client.post(path, headers=auth(review=True), json=criteria).status_code == 409
    state = "succeeded"
    assert (
        client.post(path, headers=auth("tenant_b", review=True), json=criteria).status_code == 404
    )
    assert client.post(path, headers=auth(review=True), json=criteria).status_code == 201
    assert client.post(path, headers=auth(review=True), json=criteria).status_code == 409
