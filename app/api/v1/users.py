from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.core.rbac import Access, Screen, require
from app.core.security import hash_password
from app.models import User
from app.models.enums import AuditAction
from app.schemas.users import UserAdminRow, UserCreateIn, UserPatchIn

router = APIRouter(prefix="/users", tags=["users"])
admin = require(Screen.users)


def _get(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user


@router.get("", response_model=list[UserAdminRow], dependencies=[Depends(admin)])
def list_users(db: Session = Depends(get_db)) -> list[UserAdminRow]:
    # shortcut: unpaginated; staff lists are tens of rows, paginate if this ever holds a real directory.
    return [UserAdminRow.model_validate(u) for u in db.scalars(select(User).order_by(User.full_name))]


@router.post("", response_model=UserAdminRow, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreateIn, request: Request, access: Access = Depends(admin),
                db: Session = Depends(get_db)) -> UserAdminRow:
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "A user with this email already exists")
    user = User(email=email, full_name=body.full_name.strip(), job_title=body.job_title.strip(), role=body.role,
                password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    audit.record(db, request, AuditAction.create, entity_type="user", entity_id=user.id,
                 detail={"email": email, "role": body.role})
    return UserAdminRow.model_validate(user)


@router.patch("/{user_id}", response_model=UserAdminRow)
def update_user(user_id: int, body: UserPatchIn, request: Request, access: Access = Depends(admin),
                db: Session = Depends(get_db)) -> UserAdminRow:
    user = _get(db, user_id)
    changes = body.model_dump(exclude_unset=True, exclude={"password"})
    if user.id == access.user.id and (changes.get("is_active") is False or changes.get("role", user.role) != user.role):
        raise HTTPException(400, "You can't deactivate your own account or change your own role")
    for field, value in changes.items():
        if value is not None:
            setattr(user, field, value)
    if body.password:
        user.password_hash = hash_password(body.password)
    db.flush()
    # Never log the password itself, only that it was reset.
    audit.record(db, request, AuditAction.update, entity_type="user", entity_id=user.id,
                 detail={"changes": {k: str(v) for k, v in changes.items() if v is not None},
                         "password_reset": bool(body.password)})
    return UserAdminRow.model_validate(user)


@router.delete("/{user_id}", response_model=UserAdminRow)
def deactivate_user(user_id: int, request: Request, access: Access = Depends(admin),
                    db: Session = Depends(get_db)) -> UserAdminRow:
    """Deactivates (never deletes) so audit references stay valid; the user's session stops working at once."""
    user = _get(db, user_id)
    if user.id == access.user.id:
        raise HTTPException(400, "You can't deactivate your own account")
    user.is_active = False
    db.flush()
    audit.record(db, request, AuditAction.delete, entity_type="user", entity_id=user.id,
                 detail={"email": user.email, "deactivated": True})
    return UserAdminRow.model_validate(user)
