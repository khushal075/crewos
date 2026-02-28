import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request, FastAPI


class RequestIDMiddleware(BaseHTTPMiddleware):

    def __init__(self, app: FastAPI):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response