from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from crewos.api.routes import router as api_router
from crewos.api.metrics_routes import router as metrics_router
from crewos.api.feedback_routes import router as feedback_router
from crewos.api.approval_routes import router as approval_router
from crewos.api.agent_routes import router as agent_router
from crewos.api.document_routes import router as document_router
from crewos.api.middleware.document_body_limit import DocumentBodyLimitMiddleware
from crewos.api.middleware.request_id import RequestIDMiddleware
from crewos.api.middleware.logging import LoggingMiddleware


app = FastAPI(title="CrewAI platform", version="0.1.0")

# Keep request-ID/logging middleware outside admission so rejected bodies are observable.
app.add_middleware(DocumentBodyLimitMiddleware)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # your React port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================
# Request Id Middleware
# =====================
app.add_middleware(RequestIDMiddleware)


# ======================
# Logging Middleware
# =====================
app.add_middleware(LoggingMiddleware)


app.include_router(api_router)
app.include_router(agent_router)

app.include_router(document_router)

app.include_router(approval_router)
app.include_router(feedback_router)
app.include_router(metrics_router)
