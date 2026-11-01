"""Verify actual Celery failure logging while preserving backend failure data."""

import logging
from unittest.mock import patch
from uuid import uuid4

import pytest
from celery import Celery
from celery.app.trace import build_tracer

from crewos.core.tenant import tenant_queue
from crewos.infrastructure.celery_app import celery_app
from crewos.infrastructure.celery_log_privacy import (
    CrewTaskFailurePrivacyFilter,
    install_celery_failure_privacy,
)
from crewos.workers.agent_task import run_agent
from crewos.workers.crew_task import run_crew

SECRET = "private-provider-exception-and-input"


@pytest.mark.parametrize("kind", ["agent", "workflow"])
def test_trace_failure_logs_hide_content_and_backend_preserves_failure(kind, caplog):
    identifier = str(uuid4())
    memory_app = Celery("failure-privacy", broker="memory://", backend="cache+memory://")
    backend = memory_app.backend
    task = (run_agent if kind == "agent" else run_crew)._get_current_object()
    args = (
        ({"agent_run_id": identifier, "tenant_id": "tenant_a", "message": SECRET},)
        if kind == "agent"
        else (
            "tenant_a",
            {"input": {"message": SECRET}},
        )
    )
    target = (
        "crewos.workers.agent_task.execute_agent_job"
        if kind == "agent"
        else "crewos.workers.crew_task.CrewRunner.run"
    )
    with (
        patch.object(task, "_backend", backend),
        patch(target, side_effect=RuntimeError(SECRET)),
        caplog.at_level(logging.INFO, logger="celery.app.trace"),
    ):
        result = build_tracer(task.name, task, app=celery_app)(
            identifier,
            args,
            {},
            request={
                "id": identifier,
                "delivery_info": {"routing_key": tenant_queue("tenant_a")},
            },
        )
    assert result.info.state == "FAILURE"
    assert backend.get_state(identifier) == "FAILURE"
    assert SECRET in str(backend.get_result(identifier))
    records = [record for record in caplog.records if record.name == "celery.app.trace"]
    assert records
    assert identifier in records[-1].getMessage()
    assert "RuntimeError" in records[-1].getMessage()
    assert SECRET not in caplog.text
    assert SECRET not in str(records[-1].data)
    assert records[-1].exc_info is None


def test_expected_error_without_traceback_uses_generic_classification():
    data = {
        "name": run_agent.name,
        "id": "run-1",
        "exc": SECRET,
        "args": SECRET,
        "kwargs": SECRET,
        "traceback": SECRET,
    }
    record = logging.LogRecord(
        "celery.app.trace", logging.INFO, __file__, 1, "%(exc)s", (data,), None
    )
    record.data = data
    record.exc_text = SECRET
    record.stack_info = SECRET
    assert CrewTaskFailurePrivacyFilter().filter(record)
    assert record.getMessage().endswith("(TaskError)")
    assert SECRET not in str(record.__dict__)


@pytest.mark.parametrize(
    "data", [None, "unstructured", {"name": "other.task", "exc": SECRET}, {"name": run_agent.name}]
)
def test_unrelated_or_nonfailure_records_are_unchanged(data):
    record = logging.LogRecord(
        "celery.app.trace", logging.INFO, __file__, 1, "ordinary event", (), None
    )
    record.data = data
    assert CrewTaskFailurePrivacyFilter().filter(record)
    assert record.getMessage() == "ordinary event"
    assert record.data == data


def test_filter_installation_is_idempotent():
    install_celery_failure_privacy()
    install_celery_failure_privacy()
    logger = logging.getLogger("celery.app.trace")
    assert sum(isinstance(item, CrewTaskFailurePrivacyFilter) for item in logger.filters) == 1
