from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crewos.api.middleware.logging import LoggingMiddleware


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(LoggingMiddleware)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return TestClient(app)


def test_logs_url_path_separately_from_method(client):
    with patch("crewos.api.middleware.logging.logger") as logger:
        response = client.get("/health")

    assert response.status_code == 200
    logger.info.assert_called_once()
    event = logger.info.call_args.args[0]
    assert event["path"] == "/health"
    assert event["method"] == "GET"


def test_logs_tenant_header_instead_of_request_header(client):
    with patch("crewos.api.middleware.logging.logger") as logger:
        response = client.get(
            "/health",
            headers={
                "X-Tenant-ID": "tenant-demo",
                "X-Request-ID": "client-request-demo",
            },
        )

    assert response.status_code == 200
    logger.info.assert_called_once()
    event = logger.info.call_args.args[0]
    assert event["tenant_id"] == "tenant-demo"
