from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.api.v1.calls import customer_masked
from app.core import audit
from app.core.db import get_db, utcnow
from app.core.masking import mask_phone
from app.core.rbac import Access, Level, Screen, require
from app.models import Call, Customer, Escalation, Policy, User
from app.models.enums import AuditAction, EscalationStatus, Priority, Role
from app.schemas.escalations import EscalationRow, ResolveIn

router = APIRouter(prefix="/escalations", tags=["escalations"])
act = require(Screen.escalations, Level.full)

PRIORITY_RANK = case({Priority.urgent: 0, Priority.high: 1, Priority.medium: 2, Priority.low: 3}, value=Escalation.priority)


def _row(db: Session, e: Escalation, masked: bool) -> EscalationRow:
    c = db.get(Customer, e.customer_id)
    p = db.get(Policy, e.policy_id) if e.policy_id else None
    call = db.get(Call, e.call_id) if e.call_id else None
    assignee = db.get(User, e.assigned_to) if e.assigned_to else None
    return EscalationRow(
        id=e.id, call_id=e.call_id, call_ref=call.call_ref if call else None, customer_id=c.id,
        customer_name=c.full_name, phone=mask_phone(c.phone) if masked else c.phone, city=c.city,
        preferred_language=c.preferred_language, policy_no=p.policy_no if p else None,
        plan_name=p.plan_name if p else None, premium_inr=p.premium_inr if p else None, reason=e.reason,
        priority=e.priority, status=e.status, assigned_to=e.assigned_to,
        assigned_to_name=assignee.full_name if assignee else None, created_at=e.created_at,
        resolved_at=e.resolved_at, resolution_note=e.resolution_note, call_summary=call.summary if call else None)


@router.get("", response_model=list[EscalationRow])
def list_escalations(
    status: EscalationStatus | None = Query(None, description="Omit for the active queue (open + assigned)"),
    access: Access = Depends(require(Screen.escalations)),
    db: Session = Depends(get_db),
) -> list[EscalationRow]:
    stmt = select(Escalation)
    if status:
        stmt = stmt.where(Escalation.status == status).order_by(Escalation.created_at.desc())
    else:
        stmt = (stmt.where(Escalation.status != EscalationStatus.resolved)
                .order_by(PRIORITY_RANK, Escalation.created_at))  # most urgent, longest waiting first
    masked = customer_masked(access.user)
    # shortcut: capped at 100 rows, no pagination; paginate if the queue ever grows past that.
    return [_row(db, e, masked) for e in db.scalars(stmt.limit(100))]


def _get(db: Session, escalation_id: int) -> Escalation:
    e = db.get(Escalation, escalation_id)
    if not e:
        raise HTTPException(404, "Escalation not found")
    if e.status == EscalationStatus.resolved:
        raise HTTPException(409, "This escalation is already resolved")
    return e


@router.post("/{escalation_id}/claim", response_model=EscalationRow)
def claim(escalation_id: int, request: Request, access: Access = Depends(act),
          db: Session = Depends(get_db)) -> EscalationRow:
    e = _get(db, escalation_id)
    if e.assigned_to not in (None, access.user.id):
        raise HTTPException(409, "Another agent has already claimed this escalation")
    e.assigned_to, e.status = access.user.id, EscalationStatus.assigned
    db.commit()
    audit.record(db, request, AuditAction.update, entity_type="escalation", entity_id=e.id,
                 detail={"action": "claim"})
    return _row(db, e, customer_masked(access.user))


@router.post("/{escalation_id}/resolve", response_model=EscalationRow)
def resolve(escalation_id: int, body: ResolveIn, request: Request, access: Access = Depends(act),
            db: Session = Depends(get_db)) -> EscalationRow:
    e = _get(db, escalation_id)
    # A team lead may close anyone's escalation; an agent only an unclaimed one or their own.
    if e.assigned_to not in (None, access.user.id) and access.user.role != Role.team_lead:
        raise HTTPException(409, "Another agent has claimed this escalation")
    e.assigned_to = e.assigned_to or access.user.id
    e.status, e.resolved_at, e.resolution_note = EscalationStatus.resolved, utcnow(), body.note.strip()
    db.commit()
    audit.record(db, request, AuditAction.update, entity_type="escalation", entity_id=e.id,
                 detail={"action": "resolve"})
    return _row(db, e, customer_masked(access.user))
