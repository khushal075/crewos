"""
HTTP input channel for plain text source registration.
"""

from hmac import compare_digest
from typing import Annotated, TypeVar
from fastapi import APIRouter, HTTPException, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPBearer
from pydantic import UUID4, BaseModel, ValidationError

from crewos_ingestion.contracts.context_contracts.caller_context import CallerContext
from crewos_ingestion.contracts.input_contracts.source_registration import SourceSubmission
from crewos_ingestion.contracts.output_contracts.source_registration import SourceAcceptanceReceipt

from crewos_ingestion.lifecycle.registration.submit_source import SubmitSource
from crewos_ingestion.storage.source_store import (
    SourceDeletedConflict,
    SourceNotFound,
    SourceRegistrationConflict,
    SourceVersionConflict,
)
from crewos_ingestion.submission_checks.checks import SourceSubmissionDenied

from crewos_ingestion.contracts.input_contracts.source_lifecycle import (
    SourceDeleteRequest,
    SourceReadRequest,
    SourceUpdateBody,
    SourceUpdateRequest,
)
from crewos_ingestion.contracts.output_contracts.source_lifecycle import (
    SourceDeleteReceipt,
    SourceReadResult,
    SourceUpdateReceipt,
)
from crewos_ingestion.domain.source import SourceVersionLimitReached
from crewos_ingestion.lifecycle.update_source import UpdateSource, SourceUpdateDenied
from crewos_ingestion.lifecycle.delete_source import DeleteSource, SourceDeleteDenied
from crewos_ingestion.lifecycle.read_source import ReadSource, SourceReadDenied

MAX_REQUEST_BYTES = 8_000_000
_Body = TypeVar("_Body", bound=BaseModel)


class SourceHTTPChannel:
    """
    Authenticate and translate HTTP request into source submission.
    """

    def __init__(
        self,
        *,
        submit_source: SubmitSource,
        read_source: ReadSource,
        update_source: UpdateSource,
        delete_source: DeleteSource,
        caller_context: CallerContext,
        api_token: str,
    ) -> None:
        self._submit_source = submit_source
        self._read_source = read_source
        self._update_source = update_source
        self._delete_source = delete_source
        self._caller_context = caller_context
        self._expected_token = api_token.encode("ascii")
        self._bearer = HTTPBearer(auto_error=False)
        self.router = APIRouter()
        self.router.add_api_route(
            "/sources",
            self.register_source,
            methods=["POST"],
            response_model=SourceAcceptanceReceipt,
        )
        self.router.add_api_route(
            "/sources/{source_id}",
            self.get_source,
            methods=["GET"],
            response_model=SourceReadResult,
        )

        self.router.add_api_route(
            "/sources/{source_id}",
            self.replace_source,
            methods=["PUT"],
            response_model=SourceUpdateReceipt,
        )

        self.router.add_api_route(
            "/sources/{source_id}",
            self.remove_source,
            methods=["DELETE"],
            response_model=SourceDeleteReceipt,
        )

    async def _authenticate(self, request: Request) -> CallerContext:
        credentials = await self._bearer(request)
        if credentials is None or not compare_digest(
            credentials.credentials.encode("utf8"), self._expected_token
        ):
            raise HTTPException(
                status_code=401,
                detail="Valid bearer token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return self._caller_context

    async def _read_body(self, request: Request, model: type[_Body]) -> _Body:
        media_type = request.headers.get("content-type", "").split(";", 1)[0]
        if media_type.lower() != "application/json":
            raise HTTPException(status_code=415, detail="Expected application/json")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Request body is too long")
            body.extend(chunk)

        try:
            return model.model_validate_json(body)
        except ValidationError:
            raise HTTPException(status_code=422, detail="Invalid source submission") from None

    async def register_source(
        self,
        request: Request,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-key", pattern=r"^[A-Za-z0-9_.:-]{1,128}$"),
        ] = None,
    ) -> SourceAcceptanceReceipt:
        context = await self._authenticate(request)
        submission = await self._read_body(request, SourceSubmission)

        try:
            return await run_in_threadpool(
                self._submit_source.execute,
                submission=submission,
                context=context,
                idempotency_key=idempotency_key,
            )
        except SourceSubmissionDenied:
            raise HTTPException(status_code=403, detail="Source Creation denied") from None
        except SourceDeletedConflict:
            raise HTTPException(
                status_code=409, detail="Idempotency key belongs to a deleted source"
            ) from None
        except SourceRegistrationConflict:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key already used for different submission.",
            ) from None

    async def get_source(
        self,
        request: Request,
        source_id: UUID4,
        version: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    ) -> SourceReadResult:
        context = await self._authenticate(request)
        read_request = SourceReadRequest(source_id=source_id, version=version)

        try:
            return await run_in_threadpool(
                self._read_source.execute,
                request=read_request,
                context=context,
            )
        except SourceReadDenied:
            raise HTTPException(status_code=403, detail="Source read denied") from None

        except SourceNotFound:
            raise HTTPException(status_code=404, detail="Source version is unavailable") from None

    async def replace_source(self, request: Request, source_id: UUID4) -> SourceUpdateReceipt:
        context = await self._authenticate(request)
        body = await self._read_body(request, SourceUpdateBody)
        update_request = SourceUpdateRequest(source_id=source_id, **body.model_dump())
        try:
            return await run_in_threadpool(
                self._update_source.execute, request=update_request, context=context
            )
        except SourceUpdateDenied:
            raise HTTPException(status_code=403, detail="Source update denied") from None
        except SourceNotFound:
            raise HTTPException(status_code=404, detail="Source version is unavailable") from None
        except (SourceVersionConflict, SourceVersionLimitReached) as error:
            raise HTTPException(status_code=409, detail=str(error)) from None

    async def remove_source(self, request: Request, source_id: UUID4) -> SourceDeleteReceipt:
        context = await self._authenticate(request)
        try:
            return await run_in_threadpool(
                self._delete_source.execute,
                request=SourceDeleteRequest(source_id=source_id),
                context=context,
            )
        except SourceDeleteDenied:
            raise HTTPException(status_code=403, detail="Source delete denied") from None
        except SourceNotFound:
            raise HTTPException(status_code=404, detail="Source version is unavailable") from None
