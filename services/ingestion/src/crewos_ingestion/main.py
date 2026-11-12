"""
Assemble the standalone CrewOs ingestion application.
"""

import os
from fastapi import FastAPI, HTTPException

from crewos_ingestion.contracts.context_contracts.caller_context import CallerContext
from crewos_ingestion.input_channels.http import SourceHTTPChannel
from crewos_ingestion.lifecycle.registration.submit_source import SubmitSource
from crewos_ingestion.storage.sqlite_source_store import SQLiteSourceStore
from crewos_ingestion.submission_checks.checks import SubmissionChecks

def create_app() -> FastAPI:
    """
    Read deployment settings and wire ingestion component together.
    :return:
    """
    token = os.environ.get("INGESTION_API_TOKEN", "")
    if not token or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError("Set INGESTION_API_TOKEN to a non-empty ASCII token.")

    tenant_id = os.environ.get("INGESTION_TENANT_ID")
    if not tenant_id:
        raise ValueError("Set INGESTION_TENANT_ID")
    permissions = frozenset(
        permission.strip()
        for permission in os.environ.get("INGESTION_PERMISSIONS", "").split(",")
        if permission.strip()
    )
    caller_context = CallerContext(
        tenant_id=tenant_id,
        permissions=permissions,
    )
    store = SQLiteSourceStore(
        os.environ.get("INGESTION_DATABASE_PATH", "source.db"),
    )
    submit_source = SubmitSource(
        checks=SubmissionChecks(),
        store=store,
    )

    channel = SourceHTTPChannel(
        submit_source=submit_source,
        caller_context=caller_context,
        api_token=token,
    )

    app = FastAPI(title="CrewOs Ingestion Service")
    app.include_router(channel.router)
    return app