from sqlalchemy import select

from app.core.db import utcnow
from app.models import Call
from app.models.enums import Speaker
from app.services.voice_tools import CallState, add_turn


def test_concurrent_turns_get_distinct_seq(db, client):
    """The bot saves turns from separate sessions; uncommitted turns must not reuse a seq (React keys)."""
    call = db.scalars(select(Call).order_by(Call.id)).first()
    state = CallState(call_id=call.id, started_at=utcnow())
    factory = client.app.state.session_factory
    with factory() as a, factory() as b:  # neither has committed when the other picks its seq
        first = add_turn(a, state, Speaker.customer, "Hello")
        second = add_turn(b, state, Speaker.bot, "Hi")
        assert second.seq == first.seq + 1
        a.rollback()
        b.rollback()
