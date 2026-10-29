from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, customers, rbac
from app.core.audit import install_audit_middleware
from app.core.config import get_settings
from app.core.db import SessionLocal


def create_app() -> FastAPI:
    app = FastAPI(title="AssureVoice AI API", version="1.0.0", openapi_url="/openapi.json")
    app.state.session_factory = SessionLocal

    install_audit_middleware(app)
    app.add_middleware(CORSMiddleware, allow_origins=[get_settings().frontend_origin],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    v1 = APIRouter(prefix="/api/v1")
    for module in (auth, customers, rbac):
        v1.include_router(module.router)
    app.include_router(v1)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
