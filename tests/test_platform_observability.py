from fastapi.testclient import TestClient
from pydantic import SecretStr

from crewos.core.config import settings
from crewos.infrastructure.agent_metrics import agent_metrics
from crewos.main import app


def test_metrics_endpoint_is_opt_in_and_authenticated(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    client = TestClient(app)
    monkeypatch.setattr(settings, "AGENT_METRICS_TOKEN", None)
    assert client.get("/metrics").status_code == 404
    monkeypatch.setattr(settings, "AGENT_METRICS_TOKEN", SecretStr("metrics-fixture"))
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 403
    agent_metrics.observe(operation="run", outcome="success", duration_seconds=0.12)
    response = client.get("/metrics", headers={"Authorization": "Bearer metrics-fixture"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'crewos_agent_operations_total{operation="run",outcome="success"}' in response.text
    assert "metrics-fixture" not in response.text
