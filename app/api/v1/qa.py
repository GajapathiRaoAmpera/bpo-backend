from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.core import audit
from app.core.db import get_db, utcnow
from app.core.formatting import format_date
from app.core.rbac import Access, Level, Screen, require
from app.models import Call, Customer, QAScore, TranscriptTurn, User
from app.models.enums import AuditAction, CallOutcome
from app.schemas.qa import QADetail, QAPage, QAReviewIn, QARow
from app.schemas.voice import TurnOut

router = APIRouter(prefix="/qa", tags=["qa"])
read = require(Screen.qa)

CHECKS = ("ai_disclosure", "identity_verified", "correct_figures", "no_mis_selling", "polite_close")
Reviewer = aliased(User)


def _base():
    # Ended calls where the customer answered; QA may be missing if automatic scoring failed.
    return (select(Call, QAScore, Customer.full_name, Reviewer.full_name)
            .outerjoin(QAScore, QAScore.call_id == Call.id)
            .outerjoin(Customer, Customer.id == Call.customer_id)
            .outerjoin(Reviewer, Reviewer.id == QAScore.reviewer_id)
            .where(Call.ended_at.is_not(None), Call.outcome != CallOutcome.no_answer))


def _row(call: Call, qa: QAScore | None, customer: str | None, reviewer: str | None) -> dict:
    return dict(call_id=call.id, call_ref=call.call_ref, mode=call.mode, started_at=call.started_at,
                duration_sec=call.duration_sec, customer_name=customer, outcome=call.outcome,
                score=qa.score if qa else None, source=qa.source if qa else None,
                **{k: getattr(qa, k) if qa else None for k in CHECKS},
                reviewer_name=reviewer, reviewer_note=qa.reviewer_note if qa else None,
                locked_at=qa.locked_at if qa else None)


@router.get("/queue", response_model=QAPage)
def qa_queue(state: Literal["open", "locked", "all"] = "open", page: int = Query(1, ge=1),
             page_size: int = Query(25, ge=1, le=100), access: Access = Depends(read),
             db: Session = Depends(get_db)) -> QAPage:
    """Open = not locked yet, lowest scores first so failures get reviewed before passes."""
    stmt = _base()
    if state == "open":
        stmt = stmt.where(QAScore.locked_at.is_(None)).order_by(QAScore.score.is_not(None), QAScore.score,
                                                                Call.started_at.desc())
    else:
        if state == "locked":
            stmt = stmt.where(QAScore.locked_at.is_not(None))
        stmt = stmt.order_by(Call.started_at.desc())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return QAPage(items=[QARow(**_row(*r)) for r in rows], total=total, page=page, page_size=page_size)


def _detail(db: Session, call_id: int) -> QADetail:
    r = db.execute(_base().where(Call.id == call_id)).first()
    if not r:
        raise HTTPException(404, "Call not found in the QA queue")
    call, customer = r[0], db.get(Customer, r[0].customer_id) if r[0].customer_id else None
    turns = []
    for t in db.scalars(select(TranscriptTurn).where(TranscriptTurn.call_id == call_id)
                        .order_by(TranscriptTurn.offset_ms, TranscriptTurn.seq)):
        out = TurnOut.model_validate(t)
        if customer:  # QA doesn't need the spoken date of birth; never send it to a masked role
            for dob in (format_date(customer.dob), customer.dob.isoformat()):
                out.text = out.text.replace(dob, "[date of birth]")
        turns.append(out)
    return QADetail(**_row(*r), summary=call.summary, turns=turns)


@router.get("/{call_id}", response_model=QADetail)
def qa_detail(call_id: int, access: Access = Depends(read), db: Session = Depends(get_db)) -> QADetail:
    return _detail(db, call_id)


@router.put("/{call_id}", response_model=QADetail)
def review_call(call_id: int, body: QAReviewIn, request: Request,
                access: Access = Depends(require(Screen.qa, Level.full)), db: Session = Depends(get_db)) -> QADetail:
    _detail(db, call_id)  # 404 unless the call is reviewable
    qa = db.get(QAScore, call_id)
    if qa and qa.locked_at:
        raise HTTPException(409, "This evaluation is locked and can't be changed")
    before = {k: getattr(qa, k) for k in (*CHECKS, "score")} if qa else None
    qa = qa or QAScore(call_id=call_id)
    checks = body.model_dump(include=set(CHECKS))
    for k, v in checks.items():
        setattr(qa, k, v)
    qa.score, qa.source, qa.reviewer_id, qa.reviewer_note = sum(checks.values()), "human", access.user.id, body.note
    if body.lock:
        qa.locked_at = utcnow()
    db.add(qa)
    db.commit()
    audit.record(db, request, AuditAction.update, entity_type="qa_score", entity_id=call_id,
                 detail={"before": before, "after": {**checks, "score": qa.score}, "locked": body.lock})
    return _detail(db, call_id)
