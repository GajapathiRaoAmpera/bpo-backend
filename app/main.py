import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api.v1 import auth, customers, rbac, voice, audit, integrations, live, users
from app.api.v1 import calls, dashboard, escalations, followups, policies
from app.api.v1 import approvals, campaigns, qa, scripts
from app.core.audit import install_audit_middleware
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.ratelimit import limiter, rate_limited


@asynccontextmanager
async def lifespan(app: FastAPI):
    from pipecat.transports.smallwebrtc.connection import IceServer
    from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler

    app.state.webrtc = SmallWebRTCRequestHandler(ice_servers=[IceServer(urls="stun:stun.l.google.com:19302")])
    scheduler = None
    if not os.environ.get("PYTEST_CURRENT_TEST"):  # tests call the job function directly
        from app.services.followup_scheduler import start_scheduler
        scheduler = start_scheduler(app.state.session_factory)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    await app.state.webrtc.close()


def create_app() -> FastAPI:
    s = get_settings()
    prod = s.app_env == "production"
    # No /docs, /redoc or /openapi.json in production; frontend types are generated against sandbox.
    app = FastAPI(title="AssureVoice AI API", version="1.0.0", lifespan=lifespan,
                  openapi_url=None if prod else "/openapi.json")
    app.state.session_factory = SessionLocal
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limited)

    install_audit_middleware(app)
    app.add_middleware(CORSMiddleware, allow_origins=[s.frontend_origin],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    security_headers = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                        "Referrer-Policy": "strict-origin-when-cross-origin"}
    if s.cookie_secure:  # HSTS only when served over HTTPS
        security_headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(security_headers)
        return response

    v1 = APIRouter(prefix="/api/v1")
    for module in (auth, customers, rbac, voice, audit, integrations, live, users,
                   calls, dashboard, escalations, followups, policies,
                   approvals, campaigns, qa, scripts):
        v1.include_router(module.router)
    app.include_router(v1)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
