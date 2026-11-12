"""
HTTP input channel for plain text source registration.
"""

from hmac import compare_digest
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Header, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from crewos_ingestion.contracts.context_contracts.caller_context import CallerContext
from crewos_ingestion.contracts.input_contracts.source_registration import SourceSubmission
from crewos_ingestion.contracts.output_contracts.source_registration import SourceAcceptanceReceipt

from crewos_ingestion.lifecycle.registration.submit_source import SubmitSource
from crewos_ingestion.storage.source_store import SourceRegistrationConflict
from crewos_ingestion.submission_checks.checks import SourceSubmissionDenied

MAX_REQUEST_BYTES = 8_000_000

bearer = HTTPBearer(auto_error=False)

class SourceHTTPChannel:
    """
    Authenticate and translate HTTP request into source submission.
    """

    def __init__(
        self,
        *,
        submit_source: SubmitSource,
        caller_context: CallerContext,
        api_token: str,
    )->None:
        self._submit_source = submit_source
        self._caller_context = caller_context
        self._expected_token = api_token.encode('ascii')
        self._bearer = HTTPBearer(auto_error=False)
        self.router = APIRouter()
        self.router.add_api_route(
            "/sources",
            self.register_source,
            methods=["POST"],
            response_model=SourceAcceptanceReceipt,
        )

    async def _authenticate(self, request: Request) -> CallerContext:
        credentials = await self._bearer(request)
        base_condition = credentials is None
        condition = base_condition or not compare_digest(
            credentials.credentials.encode('utf8'),
            self._expected_token
        )
        if condition:
            raise HTTPException(
                status_code=401,
                detail="Valid bearer token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return self._caller_context

    async def _read_submission(self, request: Request) -> SourceSubmission:
        media_type = request.headers.get("content-type", "").split(";", 1)[0]
        if media_type.lower() != "application/json":
            raise HTTPException(status_code=415, detail="Expected application/json")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Request body is too long")
            body.extend(chunk)

        try:
            return SourceSubmission.model_validate_json(body)
        except ValidationError as err:
            raise HTTPException(status_code=422, detail="Invalid source submission") from None



    async def register_source(
            self,
            request: Request,
            idempotency_key: Annotated[
                str | None,
                Header(alias="Idempotency-key", pattern=r"^[A-Za-z0-9_.:-]{1,128}$"),
            ] = None,
    )-> SourceAcceptanceReceipt:
        context = await self._authenticate(request)
        submission = await self._read_submission(request)

        try:
            return await run_in_threadpool(
                self._submit_source.execute,
                submission=submission,
                context=context,
                idempotency_key=idempotency_key,
            )
        except SourceSubmissionDenied:
            raise HTTPException(
                status_code=403,
                detail="Source Creation denied"
            ) from None
        except SourceRegistrationConflict:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key already used for different submission.",
            ) from None



