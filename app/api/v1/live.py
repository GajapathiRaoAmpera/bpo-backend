from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db, utcnow
from app.core.masking import mask_phone
from app.core.rbac import MATRIX, Access, Level, Screen, require
from app.models import Call, Customer, Policy, TranscriptTurn, User
from app.models.enums import CallStatus, Speaker
from app.schemas.live import LiveCallRow
from app.voice.session import SESSIONS

router = APIRouter(prefix="/live", tags=["live"])


@router.get("/calls", response_model=list[LiveCallRow])
def live_calls(access: Access = Depends(require(Screen.live_monitor)), db: Session = Depends(get_db)) -> list[LiveCallRow]:
    """Calls in progress right now (real agent-console sessions; the sandbox has no simulated live floor)."""
    # Masking follows the role's customer-data level, so OM sees the same masking as on Customers.
    masked = MATRIX[Screen.customers].get(access.user.role) != Level.full
    # Masked roles see only what the bot/agent said: customer speech can contain identity details.
    speakers = [Speaker.bot, Speaker.agent] if masked else [Speaker.bot, Speaker.agent, Speaker.customer]
    rows = db.execute(
        select(Call, Customer, Policy.policy_no, User.full_name)
        .outerjoin(Customer, Customer.id == Call.customer_id)
        .outerjoin(Policy, Policy.id == Call.policy_id)
        .outerjoin(User, User.id == Call.agent_user_id)
        .where(Call.status == CallStatus.in_progress, Call.ended_at.is_(None))
        .order_by(Call.started_at.desc()).limit(100)
    ).all()
    now = utcnow()
    out = []
    for call, cust, policy_no, agent_name in rows:
        last = db.scalars(select(TranscriptTurn).where(TranscriptTurn.call_id == call.id,
                                                       TranscriptTurn.speaker.in_(speakers))
                          .order_by(TranscriptTurn.offset_ms.desc(), TranscriptTurn.seq.desc()).limit(1)).first()
        live = SESSIONS.get(call.id)
        out.append(LiveCallRow(
            call_id=call.id, call_ref=call.call_ref, mode=call.mode, direction=call.direction,
            started_at=call.started_at, duration_sec=int((now - call.started_at).total_seconds()),
            customer_name=cust.full_name if cust else None,
            customer_phone=(mask_phone(cust.phone) if masked else cust.phone) if cust else None,
            policy_no=policy_no, agent_name=agent_name, verified=call.verified,
            bot_connected=bool(live and live.runner), taken_over=call.taken_over_at is not None,
            sentiment=call.sentiment, last_speaker=last.speaker if last else None,
            last_text=last.text if last else None))
    return out
