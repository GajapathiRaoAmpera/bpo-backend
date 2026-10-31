from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.config import get_settings
from app.core.db import get_db, utcnow
from app.core.ratelimit import limiter
from app.core.rbac import LANDING, ROLE_LABELS, current_user, nav_for
from app.core.security import create_token, hash_password, verify_password
from app.models import AuditLog, User
from app.models.enums import AuditAction
from app.schemas.auth import LoginIn, MeOut, NavItem, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# Verified against when the email is unknown, so timing doesn't reveal valid accounts.
_DUMMY_HASH = hash_password("not-a-real-password")
LOCKOUT_FAILURES, LOCKOUT_WINDOW = 10, timedelta(minutes=15)


def _me(user: User) -> MeOut:
    return MeOut(user=UserOut.model_validate(user), role_label=ROLE_LABELS[user.role],
                 landing=LANDING[user.role],
                 nav=[NavItem(screen=s, level=lv) for s, lv in nav_for(user.role).items()])


def _locked(db: Session, email: str) -> bool:
    """Too many recent failed logins for this email (counted from the audit log)."""
    failures = db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == AuditAction.login_failed, AuditLog.ts >= utcnow() - LOCKOUT_WINDOW,
        AuditLog.detail["email"].as_string() == email))
    return failures >= LOCKOUT_FAILURES


@router.post("/login", response_model=MeOut)
@limiter.limit(lambda: get_settings().login_rate_limit)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> MeOut:
    email = body.email.lower()
    user = db.scalar(select(User).where(User.email == email))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    # Locked accounts get the same 401 as a wrong password, so the lock doesn't reveal valid emails.
    if not user or not ok or not user.is_active or _locked(db, email):
        audit.record(db, request, AuditAction.login_failed, user=user if user else None,
                     entity_type="user", detail={"email": email})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")

    user.last_login_at = utcnow()
    audit.record(db, request, AuditAction.login, user=user, entity_type="user", entity_id=user.id)
    s = get_settings()
    response.set_cookie(s.cookie_name, create_token(user.id, user.role, LANDING[user.role]),
                        max_age=s.jwt_ttl_minutes * 60, httponly=True, secure=s.cookie_secure,
                        samesite="lax", path="/")
    return _me(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, user: User = Depends(current_user),
           db: Session = Depends(get_db)) -> None:
    audit.record(db, request, AuditAction.logout, user=user, entity_type="user", entity_id=user.id)
    response.delete_cookie(get_settings().cookie_name, path="/")


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(current_user)) -> MeOut:
    return _me(user)
