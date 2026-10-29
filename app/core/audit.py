from typing import Any

from fastapi import FastAPI, Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User
from app.models.enums import AuditAction

_METHOD_ACTION = {"POST": AuditAction.create, "PUT": AuditAction.update,
                  "PATCH": AuditAction.update, "DELETE": AuditAction.delete}


def _client(request: Request) -> tuple[str | None, str | None]:
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else None)
    return ip, (request.headers.get("user-agent") or "")[:300] or None


def record(db: Session, request: Request, action: AuditAction, *, user: User | None = None,
           entity_type: str | None = None, entity_id: Any = None, detail: dict | None = None) -> None:
    """Explicit audit entry (logins, customer views, approvals, exports...).
    Commits immediately so the entry survives even if the caller later fails."""
    user = user or getattr(request.state, "user", None)
    ip, ua = _client(request)
    db.add(AuditLog(actor_user_id=user.id if user else None, actor_role=user.role if user else None,
                    action=action, entity_type=entity_type,
                    entity_id=None if entity_id is None else str(entity_id),
                    detail=detail, ip=ip, user_agent=ua))
    db.commit()
    request.state.audited = True


def install_audit_middleware(app: FastAPI) -> None:
    """Safety net: any successful authenticated write not already audited
    explicitly by its endpoint gets a generic create/update/delete entry."""

    @app.middleware("http")
    async def audit_writes(request: Request, call_next):
        response = await call_next(request)
        action = _METHOD_ACTION.get(request.method)
        user = getattr(request.state, "user", None)
        if (action and user and response.status_code < 400
                and request.url.path.startswith("/api/")
                and not getattr(request.state, "audited", False)):
            with request.app.state.session_factory() as db:
                ip, ua = _client(request)
                db.add(AuditLog(actor_user_id=user.id, actor_role=user.role, action=action,
                                entity_type=request.url.path.removeprefix("/api/v1/").split("/")[0],
                                detail={"method": request.method, "path": request.url.path,
                                        "status": response.status_code}, ip=ip, user_agent=ua))
                db.commit()
        return response
