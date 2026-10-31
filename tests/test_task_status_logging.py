"""Lookup logs preserve tenant privacy while exposing safe outcomes and phases."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from crewos.application.services.tool_log_context import ToolLogContext, tool_log_context
from crewos.infrastructure.task_ownership import TaskOwnershipStore
from crewos.infrastructure.task_status_reader import CeleryTaskStatusReader
from tests.helpers import MemoryRedis

TASK = "private-task-identifier"


def setup_reader():
    store = TaskOwnershipStore(MemoryRedis())
    store.register(TASK, "tenant_a")
    backend = Mock(return_value=SimpleNamespace(state="SUCCESS"))
    logger = Mock()
    reader = CeleryTaskStatusReader(store, backend, event_logger=logger)
    return reader, store, backend, logger


def test_owned_read_logs_correlation_and_mapped_status_without_task_identifier():
    reader, _, _, logger = setup_reader()
    with tool_log_context(ToolLogContext("run-1", "call-1", 1)):
        reader.read(agent_task_id=TASK, tenant_id="tenant_a")
    records = [call.args[0] for call in logger.info.call_args_list]
    assert [record["event"] for record in records] == [
        "task_ownership_checked",
        "task_status_read_completed",
    ]
    assert records[0]["outcome"] == "accessible"
    assert records[1]["status"] == "succeeded"
    assert all(
        record["agent_run_id"] == "run-1" and record["tool_call_id"] == "call-1"
        for record in records
    )
    assert TASK not in json.dumps(records)
    logger.error.assert_not_called()


def test_missing_and_cross_tenant_lookup_have_identical_log_shapes():
    records = []
    for task_id in (TASK, "nonexistent"):
        reader, _, backend, logger = setup_reader()
        reader.read(agent_task_id=task_id, tenant_id="tenant_b")
        backend.assert_not_called()
        logger.info.assert_called_once()
        records.append(logger.info.call_args.args[0])
    assert (
        records[0]
        == records[1]
        == {
            "event": "task_ownership_checked",
            "tenant_id": "tenant_b",
            "outcome": "unavailable",
        }
    )


@pytest.mark.parametrize("phase", ["ownership", "backend"])
def test_failures_log_phase_without_raw_exception_and_propagate(phase):
    reader, store, backend, logger = setup_reader()
    failure = RedisConnectionError("private-storage-credential")
    if phase == "backend":
        backend.side_effect = failure
    with (
        patch.object(store.client, "get", side_effect=failure)
        if phase == "ownership"
        else nullcontext()
    ):
        with pytest.raises(RedisConnectionError) as error:
            reader.read(agent_task_id=TASK, tenant_id="tenant_a")
    assert error.value is failure
    record = logger.error.call_args.args[0]
    assert record["phase"] == phase
    assert record["event"] == "task_status_read_failed"
    assert record["error_type"] == "ConnectionError"
    assert "private" not in json.dumps(record)
    if phase == "ownership":
        backend.assert_not_called()


def test_unsupported_backend_state_is_not_logged_raw():
    reader, _, backend, logger = setup_reader()
    backend.return_value.state = "private-unsupported-state"
    reader.read(agent_task_id=TASK, tenant_id="tenant_a")
    assert logger.info.call_args.args[0]["status"] == "unknown"
    assert "private" not in json.dumps([call.args[0] for call in logger.info.call_args_list])
