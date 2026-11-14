"""Verify HTTP source registration with a temporary SQLite database."""

import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from crewos_ingestion.input_channels import http
from crewos_ingestion.main import create_app

_BODY = {"title": "Webhook Recovery", "content": "Check delivery logs."}
_AUTH = {"Authorization": "Bearer ingestion-test-token"}


@pytest.fixture
def database_path(tmp_path, monkeypatch):
    path = tmp_path / "sources.sqlite3"
    monkeypatch.setenv("INGESTION_DATABASE_PATH", str(path))
    monkeypatch.setenv("INGESTION_API_TOKEN", "ingestion-test-token")
    monkeypatch.setenv("INGESTION_TENANT_ID", "tenant_a")
    monkeypatch.setenv("INGESTION_PERMISSIONS", "source.create")
    return path


@pytest.fixture
def client(database_path):
    with TestClient(create_app()) as app_client:
        yield app_client


def test_submission_uses_authenticated_tenant_not_request_header(client, database_path):
    response = client.post("/sources", json=_BODY, headers={**_AUTH, "X-Tenant-Id": "tenant_b"})

    assert response.status_code == 200
    receipt = response.json()
    assert receipt["version"] == 1
    assert receipt["status"] == "accepted"
    with closing(sqlite3.connect(database_path)) as db:
        assert db.execute("SELECT tenant_id, source_id FROM source_versions").fetchall() == [
            ("tenant_a", receipt["source_id"])
        ]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong-token"}])
def test_missing_or_invalid_credentials_return_401(client, headers):
    response = client.post("/sources", json=_BODY, headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_authenticated_caller_without_create_permission_cannot_write(database_path, monkeypatch):
    monkeypatch.delenv("INGESTION_PERMISSIONS", raising=False)

    with TestClient(create_app()) as client:
        response = client.post("/sources", json=_BODY, headers=_AUTH)

    assert response.status_code == 403
    with closing(sqlite3.connect(database_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM source_versions").fetchone() == (0,)


def test_identical_retry_returns_same_receipt_and_changed_input_conflicts(client):
    headers = {**_AUTH, "Idempotency-Key": "upload-42"}
    first = client.post("/sources", json=_BODY, headers=headers)
    retry = client.post("/sources", json=_BODY, headers=headers)
    conflict = client.post("/sources", json={**_BODY, "content": "Changed"}, headers=headers)

    assert first.status_code == retry.status_code == 200
    assert first.json() == retry.json()
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    "body",
    [b"{", b'{"title":"Runbook","content":"Check logs","tenant_id":"tenant_b"}'],
)
def test_invalid_submission_returns_422(client, body):
    response = client.post(
        "/sources", content=body, headers={**_AUTH, "Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid source submission"}


def test_unsupported_media_type_returns_415(client):
    response = client.post(
        "/sources", content="Check logs", headers={**_AUTH, "Content-Type": "text/plain"}
    )

    assert response.status_code == 415


def test_oversized_request_returns_413_before_json_parsing(client, monkeypatch):
    monkeypatch.setattr(http, "MAX_REQUEST_BYTES", 16)
    response = client.post(
        "/sources", content=b"x" * 17, headers={**_AUTH, "Content-Type": "application/json"}
    )

    assert response.status_code == 413
