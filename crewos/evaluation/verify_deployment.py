"""Build and verify an isolated Compose stack with synthetic credentials and data."""

import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4

import yaml

_PROBE = r"""
import json, os, sqlite3, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

def call(path, data=None, *, method=None, status=200, token="tenant-fixture", review=False):
    headers = {"X-Tenant-Id": "smoke_tenant", "Authorization": "Bearer " + token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if review:
        headers["X-Approval-Token"] = "review-fixture"
    request = Request("http://127.0.0.1:8000" + path,
                      data=json.dumps(data).encode() if data is not None else None,
                      headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            assert response.status == status, (path, response.status)
            content = response.read().decode()
            return content if path == "/metrics" else json.loads(content)
    except HTTPError as error:
        assert error.code == status, (path, error.code, error.read().decode())

call("/health")
run_id = call("/agents/runs", {"message": "Propose an incident note."}, status=202)["agent_run_id"]
run_path = f"/agents/runs/{run_id}"
deadline = time.monotonic() + 45
while True:
    run = call(run_path)
    if run["state"] not in ("queued", "running"):
        break
    assert time.monotonic() < deadline, "Timed out waiting for worker"
    time.sleep(0.2)
assert run["state"] == "succeeded" and len(run["approval_ids"]) == 1, run
action = "/agents/actions/" + run["approval_ids"][0]
pending = call(action)
assert pending["state"] == "pending" and pending["arguments"]["incident_id"] == "smoke-incident"
call(action + "/execute", method="POST", status=409)
call(action + "/review", {"approve": True}, status=403)
assert call(action + "/review", {"approve": True}, review=True)["state"] == "approved"
assert call(action + "/execute", method="POST")["note_id"]
call(action + "/execute", method="POST", status=409)
assert call(action)["state"] == "completed"
with sqlite3.connect(os.environ["AGENT_ACTION_DB_PATH"]) as db:
    assert db.execute("SELECT COUNT(*) FROM demo_incident_note").fetchone() == (1,)
call(run_path + "/feedback", {"expected_outcome": "final_answer",
     "required_phrases": ["awaits approval"]}, status=201, review=True)
from crewos.evaluation.feedback_cases import SQLiteFeedbackStore, evaluate_feedback
store = SQLiteFeedbackStore(os.environ["AGENT_FEEDBACK_DB_PATH"])
report = evaluate_feedback(store.cases(tenant="smoke_tenant"), {run_id: run["result"]})
assert report["baseline_passed"] == report["candidate_passed"] == 1
call("/metrics", status=403)
metrics = call("/metrics", token="metrics-fixture")
for operation in ("run", "model", "tool"):
    assert f'crewos_agent_operations_total{{operation="{operation}",outcome="success"}}' in metrics
print(json.dumps({"run_id": run_id, "checks": [
    "api_worker_redis_run", "provider_fallback_to_scripted_secondary", "approval_before_mutation",
    "approved_action_executed_once", "feedback_case_replayed", "authenticated_worker_metrics",
]}))
"""


def verify() -> dict:
    root = Path(__file__).resolve().parents[2]
    project = f"crewos-verify-{uuid4().hex[:10]}"
    with TemporaryDirectory(prefix="crewos-compose-") as directory:
        temp = Path(directory)
        data = yaml.safe_load((root / "docker-compose.yml").read_text())
        data["services"] = {name: data["services"][name] for name in ("backend", "worker", "redis")}
        env = temp / "fixture.env"
        environment = {
            "TENANT_ID": "smoke_tenant",
            "TENANT_API_KEYS": json.dumps({"smoke_tenant": "tenant-fixture"}),
            "TENANT_PERMISSIONS": json.dumps(
                {
                    "smoke_tenant": [
                        "agent_run.create",
                        "agent_run.read",
                        "incident_note.write",
                        "agent_action.read",
                        "agent_action.review",
                        "agent_action.execute",
                        "agent_feedback.review",
                    ]
                }
            ),
            "AGENT_ACTIONS_ENABLED": "true",
            "AGENT_ACTION_DB_PATH": "/app/var/agent_actions.sqlite3",
            "AGENT_REVIEWER_KEYS": json.dumps({"smoke_tenant": "review-fixture"}),
            "AGENT_FEEDBACK_ENABLED": "true",
            "AGENT_FEEDBACK_DB_PATH": "/app/var/agent_feedback.sqlite3",
            "AGENT_METRICS_TOKEN": "metrics-fixture",
            "CELERY_CONCURRENCY": "1",
            "LLM_BASE_URL": "http://127.0.0.1:8080/v1",
            "LLM_MODEL": "fixture-primary",
            "AGENT_FALLBACK_MODEL": json.dumps(
                {
                    "base_url": "http://127.0.0.1:8080/v1",
                    "model": "fixture-secondary",
                    "api_key": "fixture-key",
                }
            ),
        }
        env.write_text("".join(f"{key}={value}\n" for key, value in environment.items()))
        for name in ("backend", "worker"):
            service = data["services"][name]
            service["env_file"] = [str(env)]
            service["image"] = f"{project}:local"
            service.pop("build", None)
        backend = data["services"]["backend"]
        backend["build"] = str(root)
        backend.pop("ports", None)
        worker = data["services"]["worker"]
        worker["command"] = [
            "sh",
            "-c",
            "python -m crewos.evaluation.deployment_model_fixture & exec worker",
        ]
        worker["stop_grace_period"] = "10s"
        path = temp / "compose.yml"
        path.write_text(yaml.safe_dump(data))
        prefix = ["docker", "compose", "--project-name", project, "-f", str(path)]
        isolated_env = {**os.environ, "COMPOSE_DISABLE_ENV_FILE": "1"}

        def compose(*args, timeout=120):
            return subprocess.run(
                [*prefix, *args],
                cwd=temp,
                env=isolated_env,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            ).stdout

        try:
            compose("build", "backend", timeout=600)
            compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "90")
            report = json.loads(compose("exec", "-T", "backend", "python", "-c", _PROBE))
            logs = compose("logs", "--no-color", "backend", "worker")
            for event in ("agent_job_submitted", "agent_job_received", "agent_job_completed"):
                assert any(event in line and report["run_id"] in line for line in logs.splitlines())
            assert "agent_provider_fallback" in logs
            report["checks"].append("run_id_correlated_in_api_and_worker_logs")
            return {"passed": True, **report}
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f"Verification command failed:\n{error.stderr}") from None
        finally:
            cleanup = subprocess.run(
                [*prefix, "down", "--volumes", "--remove-orphans"],
                cwd=temp,
                env=isolated_env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            subprocess.run(
                ["docker", "image", "rm", f"{project}:local"],
                cwd=temp,
                env=isolated_env,
                capture_output=True,
                timeout=30,
            )
            if cleanup.returncode:
                import sys

                print(f"Compose cleanup failed for {project}: {cleanup.stderr}", file=sys.stderr)


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
