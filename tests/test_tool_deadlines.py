"""Tool timeout propagation uses isolated clients, not shared-pool mutation."""

from unittest.mock import Mock, patch

import pytest
from redis import Redis

from crewos.application.services.execution_control import (
    ExecutionControl,
    execution_scope,
    AgentDeadlineExceeded,
)
from crewos.infrastructure.tool_io import ownership_client, bounded_result


def test_ownership_connection_timeouts_use_remaining_budget_without_mutating_shared_pool():
    original = Redis(host="127.0.0.1", port=6379, socket_timeout=2, socket_connect_timeout=2)
    control = ExecutionControl()
    control.tool_remaining = Mock(return_value=0.25)
    with execution_scope(control):
        with ownership_client(original) as bounded:
            assert bounded is not original
            assert bounded.connection_pool.connection_kwargs["socket_timeout"] == 0.25
            assert bounded.connection_pool.connection_kwargs["socket_connect_timeout"] == 0.25
    assert original.connection_pool.connection_kwargs["socket_timeout"] == 2


def test_result_backend_timeout_uses_remaining_budget_and_cleans_up():
    control = ExecutionControl()
    control.tool_remaining = Mock(return_value=0.5)
    constructor = Mock()
    with execution_scope(control):
        with bounded_result("task-123", constructor):
            backend = constructor.call_args.kwargs["backend"]
            assert backend.connparams["socket_timeout"] == 0.5
            assert backend.connparams["socket_connect_timeout"] == 0.5


def test_expired_tool_scope_prevents_result_construction():
    control = ExecutionControl()
    control.tool_remaining = Mock(side_effect=AgentDeadlineExceeded("expired"))
    constructor = Mock()
    with execution_scope(control):
        with pytest.raises(AgentDeadlineExceeded):
            with bounded_result("task-123", constructor):
                pass
    constructor.assert_not_called()


def test_tool_remaining_rejects_exhausted_deadline():
    control = ExecutionControl()
    control.guard = Mock(return_value=-1)
    with pytest.raises(AgentDeadlineExceeded):
        control.tool_remaining()
