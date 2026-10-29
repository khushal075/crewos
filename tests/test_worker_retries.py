"""Worker configuration and bounded inference-retry regressions."""
from unittest.mock import patch
import httpx
import pytest
from celery.exceptions import Retry
from openai import AuthenticationError, APIConnectionError, APIStatusError
from crewos.core.config import settings
from crewos.core.tenant import tenant_queue, get_current_tenant
from crewos.utils.retry import is_retryable, retry_delay
from crewos.workers.crew_task import run_crew


def test_worker_uses_configured_tenant_and_celery_owns_signals(monkeypatch):
    from crewos.core.worker import main

    monkeypatch.setattr("sys.argv", ["worker", "--tenant-id", "tenant_b"])
    with patch("crewos.infrastructure.celery_app.celery_app.worker_main") as worker:
        main()
    args = worker.call_args.args[0]
    assert args[args.index("-Q") + 1] == tenant_queue("tenant_b")



def status_error(code, retry_after=None):
    response = httpx.Response(
        code,
        request=httpx.Request("POST", "http://model.invalid"),
        headers={} if retry_after is None else {"retry-after": retry_after},
    )
    cls = AuthenticationError if code == 401 else APIStatusError
    return cls("secret provider detail", response=response, body=None)



@pytest.mark.parametrize(
    "code,expected",
    [(400, False), (401, False), (403, False), (404, False), (429, True), (500, True), (503, True)],
)
def test_retry_classification(code, expected):
    assert is_retryable(status_error(code)) is expected



def test_connection_error_is_retryable():
    assert is_retryable(APIConnectionError(request=httpx.Request("POST", "http://model.invalid")))
    assert not is_retryable(ValueError("bad input"))



@pytest.mark.parametrize(
    "value,expected", [("10", 10.0), ("0", 0.0), ("61", None), ("-1", None), ("invalid", None)]
)
def test_retry_after_respected(value, expected):
    assert retry_delay(0, status_error(429, value)) == expected



def test_retry_jitter_is_bounded():
    for attempt in [0, 1, 5, 20]:
        assert 0 <= retry_delay(attempt, ValueError()) <= min(30, 2**attempt)



def test_credentials_are_not_retried_or_logged():
    with (
        patch("crewos.workers.crew_task.CrewRunner.run", side_effect=status_error(401)),
        patch.object(run_crew, "retry") as retry,
        patch("crewos.workers.crew_task.logger") as logger,
    ):
        with pytest.raises(AuthenticationError):
            run_crew("tenant_a", {"input": {"message": "private prompt"}})
    retry.assert_not_called()
    assert "secret provider detail" not in str(logger.mock_calls)
    assert "private prompt" not in str(logger.mock_calls)



def test_transient_failure_requests_retry():
    with (
        patch("crewos.workers.crew_task.CrewRunner.run", side_effect=status_error(503)),
        patch.object(run_crew, "retry", side_effect=Retry()) as retry,
    ):
        with pytest.raises(Retry):
            run_crew("tenant_a", {"input": {"message": "hello"}})
    assert 0 <= retry.call_args.kwargs["countdown"] <= 1



def test_retry_budget_is_enforced():
    run_crew.push_request(retries=run_crew.max_retries)
    try:
        with (
            patch("crewos.workers.crew_task.CrewRunner.run", side_effect=status_error(503)),
            patch.object(run_crew, "retry") as retry,
        ):
            with pytest.raises(APIStatusError):
                run_crew("tenant_a", {"input": {"message": "hello"}})
        retry.assert_not_called()
    finally:
        run_crew.pop_request()



def test_worker_sets_and_restores_tenant_context():
    def execute(**kwargs):
        assert get_current_tenant() == "tenant_a"
        return {"output": "done"}

    with patch("crewos.workers.crew_task.CrewRunner.run", side_effect=execute):
        assert run_crew("tenant_a", {"input": {"message": "hello"}})["output"] == "done"
    assert get_current_tenant() is None



@pytest.mark.parametrize("payload", [[], None, {"input": []}, {"input": {"message": 123}}])
def test_worker_rejects_invalid_internal_payload(payload):
    with pytest.raises(ValueError):
        run_crew("tenant_a", payload)



def test_model_adapter_disables_hidden_retries():
    from crewos.third_party.llm.ollama_adapter import OllamaLLMProvider

    with patch("crewos.third_party.llm.ollama_adapter.ChatOpenAI") as model:
        OllamaLLMProvider().get()
    assert model.call_args.kwargs["max_retries"] == 0
    assert model.call_args.kwargs["timeout"] == settings.LLM_TIMEOUT



def test_retry_after_longer_than_limit_does_not_retry():
    with (
        patch("crewos.workers.crew_task.CrewRunner.run", side_effect=status_error(429, "120")),
        patch.object(run_crew, "retry") as retry,
    ):
        with pytest.raises(APIStatusError):
            run_crew("tenant_a", {"input": {"message": "hello"}})
    retry.assert_not_called()

