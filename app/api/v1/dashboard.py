from collections import Counter
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db, utcnow
from app.core.formatting import IST
from app.core.rbac import Access, Screen, require
from app.models import Call, Escalation, Followup, Policy, QAScore
from app.models.enums import CallOutcome, EscalationStatus, FollowupKind, FollowupStatus, PolicyStatus
from app.schemas.dashboard import DashboardSummary, TrendPoint

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
viewer = require(Screen.dashboard)
Days = Query(30, ge=1, le=90)


def _window(db: Session, days: int) -> tuple[date, list[Call], list[tuple[datetime, int]]]:
    """Calls and paid renewals (payment links marked done) in the last `days` IST days."""
    first = utcnow().astimezone(IST).date() - timedelta(days=days - 1)
    since = datetime.combine(first, time.min, IST)
    calls = list(db.scalars(select(Call).where(Call.started_at >= since)))
    paid = db.execute(select(Followup.sent_at, Policy.premium_inr).join(Policy, Policy.id == Followup.policy_id)
                      .where(Followup.kind == FollowupKind.payment_link, Followup.status == FollowupStatus.done,
                             Followup.sent_at >= since)).all()
    return first, calls, [(t, inr) for t, inr in paid]


@router.get("/summary", response_model=DashboardSummary)
def summary(days: int = Days, access: Access = Depends(viewer), db: Session = Depends(get_db)) -> DashboardSummary:
    first, calls, paid = _window(db, days)
    today = utcnow().astimezone(IST).date()
    connected = [c for c in calls if c.outcome and c.outcome != CallOutcome.no_answer]
    contained = [c for c in connected if c.taken_over_at is None and c.outcome != CallOutcome.escalated]
    avg_qa = db.scalar(select(func.avg(QAScore.score)).where(QAScore.call_id.in_([c.id for c in calls])))
    return DashboardSummary(
        days=days, calls=len(calls), calls_today=sum(c.started_at.astimezone(IST).date() == today for c in calls),
        connected=len(connected), renewals=len(paid), collections_inr=sum(inr for _, inr in paid),
        ai_contained_pct=round(100 * len(contained) / len(connected), 1) if connected else 0.0,
        avg_qa_score=round(avg_qa, 2) if avg_qa is not None else None,
        open_escalations=db.scalar(select(func.count()).select_from(Escalation)
                                   .where(Escalation.status != EscalationStatus.resolved)) or 0,
        renewals_due_30d=db.scalar(select(func.count()).select_from(Policy).where(
            Policy.status.in_([PolicyStatus.due, PolicyStatus.active]),
            Policy.renewal_date.between(today, today + timedelta(days=30)))) or 0,
        outcomes={o: n for o, n in Counter(c.outcome for c in calls if c.outcome).items()},
        sentiment={s: n for s, n in Counter(c.sentiment for c in calls if c.sentiment).items()},
    )


@router.get("/trends", response_model=list[TrendPoint])
def trends(days: int = Days, access: Access = Depends(viewer), db: Session = Depends(get_db)) -> list[TrendPoint]:
    first, calls, paid = _window(db, days)
    # Bucketed in Python by IST day so the SQL stays portable (SQLite/PostgreSQL date functions differ).
    points = {first + timedelta(days=i): TrendPoint(day=first + timedelta(days=i), calls=0, renewals=0,
                                                   collections_inr=0, positive=0, neutral=0, negative=0)
              for i in range(days)}
    for c in calls:
        p = points.get(c.started_at.astimezone(IST).date())
        if p:
            p.calls += 1
            if c.sentiment:
                setattr(p, c.sentiment.value, getattr(p, c.sentiment.value) + 1)
    for t, inr in paid:
        p = points.get(t.astimezone(IST).date())
        if p:
            p.renewals += 1
            p.collections_inr += inr
    return list(points.values())
