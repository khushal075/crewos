"""Celery adapter tests use controlled request metadata, not live delivery."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crewos.application.dtos.model_decision import FinalAnswerDecision
from crewos.core.tenant import tenant_queue
from crewos.infrastructure.celery_app import celery_app
from crewos.workers.agent_task import run_agent


def job():
    return {"agent_run_id": str(uuid4()), "tenant_id": "tenant_a", "message": "Check a task"}


def invoke(payload, *, delivery_id=None, routing_key=None):
    run_agent.push_request(
        id=delivery_id if delivery_id is not None else payload["agent_run_id"],
        delivery_info={
            "routing_key": routing_key if routing_key is not None else tenant_queue("tenant_a")
        },
    )
    try:
        return run_agent.run(payload)
    finally:
        run_agent.pop_request()


def test_valid_delivery_calls_runner_and_returns_json_ready_answer():
    payload = job()
    with patch("crewos.workers.agent_task.execute_agent_job") as execute:
        execute.return_value = FinalAnswerDecision(type="final_answer", answer="Done")
        result = invoke(payload)
    execute.assert_called_once_with({**payload, "max_decisions": 5})
    assert result == {"type": "final_answer", "answer": "Done", "citations": []}


@pytest.mark.parametrize(
    "delivery_id,routing_key",
    [
        ("mismatched-id", None),
        (None, "crewai.tenant_b"),
        (None, ""),
    ],
)
def test_wrong_delivery_metadata_blocks_runner(delivery_id, routing_key):
    with patch("crewos.workers.agent_task.execute_agent_job") as execute:
        with pytest.raises(ValueError):
            invoke(job(), delivery_id=delivery_id, routing_key=routing_key)
    execute.assert_not_called()


def test_payload_permission_claim_is_rejected_before_runner():
    with patch("crewos.workers.agent_task.execute_agent_job") as execute:
        with pytest.raises(ValidationError):
            invoke({**job(), "permissions": ["agent_task.read"]})
    execute.assert_not_called()


def test_runner_failure_is_not_retried_and_does_not_log_raw_details():
    failure = RuntimeError("sensitive-provider-detail")
    with (
        patch("crewos.workers.agent_task.execute_agent_job", side_effect=failure),
        patch.object(run_agent, "retry") as retry,
        patch("crewos.workers.agent_task.logger.error") as log,
    ):
        with pytest.raises(RuntimeError) as error:
            invoke(job())
    assert error.value is failure
    retry.assert_not_called()
    assert log.call_args.args[0]["error_type"] == "RuntimeError"
    assert "sensitive-provider-detail" not in str(log.call_args)


def test_agent_task_is_registered_and_included_with_no_automatic_retry():
    assert "crewos.workers.agent_task" in celery_app.conf.include
    assert (
        celery_app.tasks["crewos.workers.agent_task.run_agent"] is run_agent._get_current_object()
    )
    assert run_agent.max_retries == 0
    assert run_agent.autoretry_for == ()


def test_busy_or_fenced_attempt_is_ignored_instead_of_overwriting_results():
    from celery.exceptions import Ignore
    from crewos.application.interfaces.run_store import RunConflict

    with patch("crewos.workers.agent_task.execute_agent_job", side_effect=RunConflict("active")):
        with pytest.raises(Ignore):
            invoke(job())


def test_ignored_duplicate_does_not_overwrite_active_backend_state():
    from celery import Celery
    from celery.app.trace import build_tracer
    from crewos.application.interfaces.run_store import RunConflict

    payload = job()
    identifier = payload["agent_run_id"]
    memory = Celery("duplicate-test", broker="memory://", backend="cache+memory://")
    backend = memory.backend
    backend.store_result(identifier, None, "STARTED")
    task = run_agent._get_current_object()
    with (
        patch.object(task, "_backend", backend),
        patch(
            "crewos.workers.agent_task.execute_agent_job",
            side_effect=RunConflict("active attempt"),
        ),
    ):
        outcome = build_tracer(task.name, task, app=celery_app)(
            identifier,
            (payload,),
            {},
            request={
                "id": identifier,
                "delivery_info": {"routing_key": tenant_queue("tenant_a")},
            },
        )
    assert outcome.info.state == "IGNORED"
    assert backend.get_state(identifier) == "STARTED"
