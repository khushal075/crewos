"""Exercise Celery's actual success tracer without a live broker/backend."""

import logging
from unittest.mock import patch
from uuid import uuid4

from celery import Celery
from celery.app.trace import build_tracer

from crewos.application.dtos.model_decision import FinalAnswerDecision
from crewos.core.tenant import tenant_queue
from crewos.infrastructure.celery_app import celery_app
from crewos.workers.agent_task import run_agent
from crewos.workers.crew_task import run_crew

SECRET = "private-generated-answer-must-not-be-logged"


def test_agent_success_log_hides_answer_but_backend_retains_it(caplog):
    identifier = str(uuid4())
    job = {"agent_run_id": identifier, "tenant_id": "tenant_a", "message": "Inspect a task"}
    memory_app = Celery("privacy-test", broker="memory://", backend="cache+memory://")
    backend = memory_app.backend
    task = run_agent._get_current_object()
    with (
        patch.object(task, "_backend", backend),
        patch(
            "crewos.workers.agent_task.execute_agent_job",
            return_value=FinalAnswerDecision(type="final_answer", answer=SECRET),
        ),
        caplog.at_level(logging.INFO, logger="celery.app.trace"),
    ):
        trace = build_tracer(task.name, task, app=celery_app)
        result = trace(
            identifier,
            (job,),
            {},
            request={
                "id": identifier,
                "delivery_info": {"routing_key": tenant_queue("tenant_a")},
            },
        )
    assert result.info is None
    expected = {"type": "final_answer", "answer": SECRET}
    assert result.retval == expected
    assert backend.get_result(identifier) == expected
    assert any("succeeded in" in record.getMessage() for record in caplog.records)
    assert SECRET not in caplog.text


def test_fixed_workflow_also_suppresses_result_representation():
    from celery.utils.saferepr import saferepr

    assert run_crew.resultrepr_maxsize == 0
    assert run_agent.resultrepr_maxsize == 0
    assert SECRET not in saferepr({"output": SECRET}, run_crew.resultrepr_maxsize)
