from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.agents.orchestrator import sync_agent_catalog
from app.api import ai, auth, automation, productivity, system
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, request_id_var
from app.database.session import SessionLocal
from app.services.seed import ensure_demo_user

configure_logging(settings.log_level)
logger = logging.getLogger("autopilot.http")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        with SessionLocal() as db:
            sync_agent_catalog(db)
            ensure_demo_user(db)
    except SQLAlchemyError:
        logger.exception("startup_database_not_ready")
    yield


_public_docs = settings.environment != "production"
app = FastAPI(title=settings.app_name, version="1.0.0", docs_url="/api/docs" if _public_docs else None, openapi_url="/api/openapi.json" if _public_docs else None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "X-CSRF-Token"],
)
register_error_handlers(app)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    token = request_id_var.set(request_id)
    started = time.monotonic()
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if settings.cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.url.path != "/api/health":
        logger.info("request", extra={"method": request.method, "path": request.url.path, "status": response.status_code, "ms": int((time.monotonic() - started) * 1000), "request_id": request_id})
    return response


for router in (
    auth.router, ai.router, productivity.emails, productivity.tasks, productivity.calendar, productivity.documents, productivity.approvals_router,
    automation.router, system.notifications, system.activity, system.insights, system.config_router, system.integrations, system.demo, system.system,
):
    app.include_router(router, prefix="/api")
