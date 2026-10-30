from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import Select, false, func, or_, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.core.formatting import IST, format_date
from app.core.rbac import MATRIX, Access, Level, Screen, require
from app.models import Call, Customer, Escalation, Followup, Policy, QAScore, TranscriptTurn, User
from app.models.enums import AuditAction, CallMode, CallOutcome, Sentiment
from app.schemas.calls import CallDetail, CallEscalation, CallFollowup, CallPage, CallRow
from app.schemas.customer import CustomerOut, PolicyOut, customer_fields
from app.schemas.voice import QAOut, TurnOut

router = APIRouter(prefix="/calls", tags=["calls"])


def customer_masked(user: User) -> bool:
    """Customer PII shown alongside calls/escalations follows the role's Customer 360 level."""
    return MATRIX[Screen.customers].get(user.role) != Level.full


def visible_calls(stmt: Select, user: User) -> Select:
    """Applies the calls access level: `own` sees only the user's calls, no access sees none."""
    level = MATRIX[Screen.calls].get(user.role)
    if level is None:
        return stmt.where(false())
    return stmt.where(Call.agent_user_id == user.id) if level == Level.own else stmt


def call_rows(db: Session, calls: list[Call]) -> list[CallRow]:
    cust_ids = {c.customer_id for c in calls if c.customer_id}
    pol_ids = {c.policy_id for c in calls if c.policy_id}
    user_ids = {c.agent_user_id for c in calls if c.agent_user_id}
    names = dict(db.execute(select(Customer.id, Customer.full_name).where(Customer.id.in_(cust_ids))).all())
    pols = dict(db.execute(select(Policy.id, Policy.policy_no).where(Policy.id.in_(pol_ids))).all())
    agents = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(user_ids))).all())
    qa = dict(db.execute(select(QAScore.call_id, QAScore.score).where(QAScore.call_id.in_([c.id for c in calls]))).all())
    return [CallRow(id=c.id, call_ref=c.call_ref, direction=c.direction, mode=c.mode, status=c.status,
                    started_at=c.started_at, duration_sec=c.duration_sec, outcome=c.outcome, sentiment=c.sentiment,
                    customer_id=c.customer_id, customer_name=names.get(c.customer_id), policy_no=pols.get(c.policy_id),
                    agent_name=agents.get(c.agent_user_id), qa_score=qa.get(c.id), is_simulated=c.is_simulated)
            for c in calls]


@router.get("", response_model=CallPage)
def list_calls(
    q: str | None = Query(None, max_length=80, description="Call ref, customer name or policy no."),
    outcome: CallOutcome | None = None,
    sentiment: Sentiment | None = None,
    mode: CallMode | None = None,
    date_from: date | None = Query(None, description="IST calendar day"),
    date_to: date | None = Query(None, description="IST calendar day, inclusive"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    access: Access = Depends(require(Screen.calls)),
    db: Session = Depends(get_db),
) -> CallPage:
    stmt = visible_calls(select(Call), access.user)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Call.call_ref.ilike(like),
                              Call.customer_id.in_(select(Customer.id).where(Customer.full_name.ilike(like))),
                              Call.policy_id.in_(select(Policy.id).where(Policy.policy_no.ilike(like)))))
    for col, val in ((Call.outcome, outcome), (Call.sentiment, sentiment), (Call.mode, mode)):
        if val:
            stmt = stmt.where(col == val)
    if date_from:
        stmt = stmt.where(Call.started_at >= datetime.combine(date_from, time.min, IST))
    if date_to:
        stmt = stmt.where(Call.started_at < datetime.combine(date_to, time.max, IST))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    calls = list(db.scalars(stmt.order_by(Call.started_at.desc(), Call.id.desc())
                            .offset((page - 1) * page_size).limit(page_size)))
    return CallPage(items=call_rows(db, calls), total=total, page=page, page_size=page_size)


@router.get("/{call_id}", response_model=CallDetail)
def get_call(call_id: int, request: Request, access: Access = Depends(require(Screen.calls)),
             db: Session = Depends(get_db)) -> CallDetail:
    call = db.scalar(visible_calls(select(Call).where(Call.id == call_id), access.user))
    if not call:
        raise HTTPException(404, "Call not found")
    masked = customer_masked(access.user)
    customer = db.get(Customer, call.customer_id) if call.customer_id else None
    policy = db.get(Policy, call.policy_id) if call.policy_id else None
    turns = [TurnOut.model_validate(t) for t in db.scalars(
        select(TranscriptTurn).where(TranscriptTurn.call_id == call.id).order_by(TranscriptTurn.offset_ms, TranscriptTurn.seq))]
    if masked and customer:  # masked roles never receive the date of birth, even spoken in a transcript
        for t in turns:
            for dob in (format_date(customer.dob), customer.dob.isoformat()):
                t.text = t.text.replace(dob, "[date of birth]")
    qa = db.get(QAScore, call.id)
    users = dict(db.execute(select(User.id, User.full_name)).all())
    escalations = db.scalars(select(Escalation).where(Escalation.call_id == call.id).order_by(Escalation.id))
    followups = db.scalars(select(Followup).where(Followup.call_id == call.id).order_by(Followup.due_at))
    # Transcripts are customer data: every view is audited.
    audit.record(db, request, AuditAction.view_customer, entity_type="call", entity_id=call.id,
                 detail={"call_ref": call.call_ref, "masked": masked})
    return CallDetail(
        **call_rows(db, [call])[0].model_dump(), ended_at=call.ended_at, verified=call.verified,
        taken_over_at=call.taken_over_at, summary=call.summary, next_action=call.next_action,
        customer=CustomerOut(**customer_fields(customer, masked)) if customer else None,
        policy=PolicyOut.model_validate(policy) if policy else None,
        turns=turns, qa=QAOut.model_validate(qa) if qa else None,
        escalations=[CallEscalation(id=e.id, reason=e.reason, priority=e.priority, status=e.status,
                                    assigned_to_name=users.get(e.assigned_to), created_at=e.created_at,
                                    resolved_at=e.resolved_at, resolution_note=e.resolution_note)
                     for e in escalations],
        followups=[CallFollowup(id=f.id, kind=f.kind, status=f.status, due_at=f.due_at, reason=f.reason,
                                message_text=f.message_text, sent_at=f.sent_at) for f in followups],
    )
