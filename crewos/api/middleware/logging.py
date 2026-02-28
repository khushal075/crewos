import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from crewos.infrastructure.logging import get_logger
from starlette.responses import Response

logger = get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        request_id = getattr(request.state, 'request_id', None)
        tenant_id = request.headers.get('X-Request-Id', None)

        # Process Request
        response: Response = await call_next(request)
        process_time = round(time.time() - start_time, 4)
        logger.info({
            "event": "http_request",
            'request_id': request_id,
            'tenant_id': tenant_id,
            'path': request.method,
            'method': request.method,
            'status_code': response.status_code,
            'duration_ms': round(process_time * 1000, 2)
        })

        return response