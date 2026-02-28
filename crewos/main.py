from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from crewos.api.routes import router as api_router
from crewos.api.middleware.request_id import RequestIDMiddleware
from crewos.api.middleware.logging import LoggingMiddleware


app = FastAPI(title="CrewAI platform", version="0.1.0")


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