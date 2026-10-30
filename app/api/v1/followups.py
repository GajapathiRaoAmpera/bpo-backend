from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.core.rbac import Access, Level, Screen, require
from app.models import Customer, Followup, Policy, User
from app.models.enums import AuditAction, FollowupKind, FollowupStatus
from app.schemas.followups import FollowupIn, FollowupPage, FollowupPatch, FollowupRow

router = APIRouter(prefix="/followups", tags=["followups"])
write = require(Screen.followups, Level.full)


def followup_rows(db: Session, items: list[Followup]) -> list[FollowupRow]:
    names = dict(db.execute(select(Customer.id, Customer.full_name)
                            .where(Customer.id.in_({f.customer_id for f in items}))).all())
    pols = dict(db.execute(select(Policy.id, Policy.policy_no).where(Policy.id.in_({f.policy_id for f in items}))).all())
    users = dict(db.execute(select(User.id, User.full_name)).all())
    return [FollowupRow(id=f.id, kind=f.kind, status=f.status, due_at=f.due_at, reason=f.reason,
                        message_text=f.message_text, payment_link_url=f.payment_link_url, sent_at=f.sent_at,
                        created_at=f.created_at, customer_id=f.customer_id, customer_name=names[f.customer_id],
                        policy_id=f.policy_id, policy_no=pols[f.policy_id], call_id=f.call_id,
                        created_by_name=users.get(f.created_by), assigned_to_name=users.get(f.assigned_to))
            for f in items]


@router.get("", response_model=FollowupPage)
def list_followups(
    status_: FollowupStatus | None = Query(None, alias="status"),
    kind: FollowupKind | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    access: Access = Depends(require(Screen.followups)),
    db: Session = Depends(get_db),
) -> FollowupPage:
    stmt = select(Followup)
    if status_:
        stmt = stmt.where(Followup.status == status_)
    if kind:
        stmt = stmt.where(Followup.kind == kind)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    # Scheduled work soonest first; history newest first.
    order = Followup.due_at.asc() if status_ == FollowupStatus.scheduled else Followup.due_at.desc()
    items = list(db.scalars(stmt.order_by(order, Followup.id).offset((page - 1) * page_size).limit(page_size)))
    return FollowupPage(items=followup_rows(db, items), total=total, page=page, page_size=page_size)


@router.post("", response_model=FollowupRow, status_code=status.HTTP_201_CREATED)
def create_followup(body: FollowupIn, request: Request, access: Access = Depends(write),
                    db: Session = Depends(get_db)) -> FollowupRow:
    policy = db.get(Policy, body.policy_id)
    if not policy:
        raise HTTPException(404, "Policy not found")
    f = Followup(policy_id=policy.id, customer_id=policy.customer_id, kind=body.kind, due_at=body.due_at,
                 reason=body.reason.strip(), status=FollowupStatus.scheduled,
                 created_by=access.user.id, assigned_to=access.user.id)
    db.add(f)
    db.commit()
    audit.record(db, request, AuditAction.create, entity_type="followup", entity_id=f.id,
                 detail={"policy_no": policy.policy_no, "kind": body.kind, "due_at": body.due_at.isoformat()})
    return followup_rows(db, [f])[0]


@router.patch("/{followup_id}", response_model=FollowupRow)
def update_followup(followup_id: int, body: FollowupPatch, request: Request, access: Access = Depends(write),
                    db: Session = Depends(get_db)) -> FollowupRow:
    f = db.get(Followup, followup_id)
    if not f:
        raise HTTPException(404, "Follow-up not found")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(422, "Nothing to update")
    if f.status in (FollowupStatus.done, FollowupStatus.cancelled):
        raise HTTPException(409, f"This follow-up is already {f.status} and can't be changed")
    if "status" in changes:
        if body.status not in (FollowupStatus.done, FollowupStatus.cancelled):
            raise HTTPException(422, "A follow-up can only be marked done or cancelled")
        if body.status == FollowupStatus.cancelled and f.status == FollowupStatus.sent:
            raise HTTPException(409, "This message was already sent; mark it done instead")
    if ("due_at" in changes or "reason" in changes) and f.status != FollowupStatus.scheduled:
        raise HTTPException(409, "Only scheduled follow-ups can be rescheduled or edited")
    before = {k: str(getattr(f, k)) for k in changes}
    for k, v in changes.items():
        setattr(f, k, v.strip() if k == "reason" else v)
    db.commit()
    audit.record(db, request, AuditAction.update, entity_type="followup", entity_id=f.id,
                 detail={"before": before, "after": {k: str(v) for k, v in changes.items()}})
    return followup_rows(db, [f])[0]
