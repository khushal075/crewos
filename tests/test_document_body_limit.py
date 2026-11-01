"""Streaming admission stops reading once the document-body limit is crossed."""

from unittest.mock import AsyncMock
import pytest
from crewos.api.middleware.document_body_limit import DocumentBodyLimitMiddleware


@pytest.mark.asyncio
async def test_overflow_stops_before_remaining_chunks_or_application():
    downstream = AsyncMock()
    receive = AsyncMock(
        side_effect=[
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": True},
            {"type": "http.request", "body": b"never-read", "more_body": False},
        ]
    )
    send = AsyncMock()
    await DocumentBodyLimitMiddleware(downstream, max_bytes=5)(
        {"type": "http", "path": "/documents", "method": "POST"}, receive, send
    )
    assert receive.await_count == 2
    downstream.assert_not_awaited()
    assert send.call_args_list[0].args[0]["status"] == 413


@pytest.mark.asyncio
async def test_disconnect_aborts_without_invoking_application():
    downstream = AsyncMock()
    await DocumentBodyLimitMiddleware(downstream, max_bytes=5)(
        {"type": "http", "path": "/documents", "method": "POST"},
        AsyncMock(return_value={"type": "http.disconnect"}),
        AsyncMock(),
    )
    downstream.assert_not_awaited()


def test_main_application_retains_request_id_on_admission_rejection(monkeypatch):
    from fastapi.testclient import TestClient
    from crewos.core.config import settings
    from crewos.main import app

    monkeypatch.setattr(settings, "DOCUMENT_MAX_REQUEST_BYTES", 128)
    response = TestClient(app).post("/documents", content=b"x" * 200)
    assert response.status_code == 413
    assert response.headers.get("X-Request-ID")
