"""Bound document request bodies before JSON parsing, including streamed bodies."""

from starlette.responses import JSONResponse
from crewos.core.config import settings


class DocumentBodyLimitMiddleware:
    def __init__(self, app, max_bytes=None):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not scope["path"].startswith("/documents")
            or scope["method"] not in {"POST", "PUT", "PATCH"}
        ):
            return await self.app(scope, receive, send)
        limit = (
            self.max_bytes if self.max_bytes is not None else settings.DOCUMENT_MAX_REQUEST_BYTES
        )
        total, chunks = 0, []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            part = message.get("body", b"")
            total += len(part)
            if total > limit:
                return await JSONResponse(
                    {"detail": "Document request too large"}, status_code=413
                )(scope, receive, send)
            chunks.append(part)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        consumed = False

        async def bounded_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
