import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, customers, rbac, voice, audit, integrations, live, users
from app.api.v1 import calls, dashboard, escalations, followups, policies
from app.api.v1 import approvals, campaigns, qa, scripts
from app.core.audit import install_audit_middleware
from app.core.config import get_settings
from app.core.db import SessionLocal


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
    app = FastAPI(title="AssureVoice AI API", version="1.0.0", openapi_url="/openapi.json", lifespan=lifespan)
    app.state.session_factory = SessionLocal

    install_audit_middleware(app)
    app.add_middleware(CORSMiddleware, allow_origins=[get_settings().frontend_origin],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

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
