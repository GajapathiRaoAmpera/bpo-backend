"""Live call registry and call finalisation (shared by the API and the bot)."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.db import utcnow
from app.models import Call, Policy, User
from app.models.enums import CallOutcome, CallStatus, Speaker
from app.services import voice_tools
from app.services.post_call import analyse_call
from app.services.voice_tools import CallState

log = logging.getLogger(__name__)


@dataclass
class LiveSession:
    state: CallState
    agent_user_id: int | None  # None for campaign phone calls (no console agent)
    runner: Any = None  # pipecat WorkerRunner while the bot is connected
    worker: Any = None
    taken_over: bool = False


# shortcut: in-process registry, so one API worker process only; move to Redis/a call service to scale out.
SESSIONS: dict[int, LiveSession] = {}
_TASKS: set[asyncio.Task] = set()


def _done(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    if not task.cancelled() and task.exception():
        log.error("Background voice task failed", exc_info=task.exception())


def spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_done)
    return task


async def stop_bot(live: LiveSession, notice: dict | None = None) -> None:
    if live.runner is None:
        return
    if notice and live.worker is not None:
        try:
            await live.worker.rtvi.send_server_message(notice)
        except Exception:
            log.warning("Could not notify client before stopping bot", exc_info=True)
    await live.runner.cancel()


def _close(db: Session, call_id: int, agent_outcome: CallOutcome | None, note: str | None, agent_id: int | None) -> bool:
    call = db.get(Call, call_id)
    if call is None or call.ended_at is not None:
        return False
    live = SESSIONS.get(call_id)
    state = live.state if live else CallState(call_id=call.id, started_at=call.started_at,
                                              verified_customer_id=call.customer_id if call.verified else None)
    # A human wrapping up after takeover can complete the same actions the bot could.
    if agent_outcome and call.policy_id and state.verified:
        policy_no = db.get(Policy, call.policy_id).policy_no
        if agent_outcome == CallOutcome.payment_link_sent and "send_payment_link" not in state.actions:
            voice_tools.send_payment_link(db, state, policy_no, created_by=agent_id)
        elif agent_outcome == CallOutcome.callback_scheduled and "schedule_followup" not in state.actions:
            voice_tools.schedule_followup(db, state, policy_no, "", "Agent scheduled a callback", created_by=agent_id)
    if note:
        voice_tools.add_turn(db, state, Speaker.agent, note.strip()[:2000])
    call.ended_at = utcnow()
    call.duration_sec = int((call.ended_at - call.started_at).total_seconds())
    escalated = "escalate_to_human" in state.actions or agent_outcome == CallOutcome.escalated
    call.status = CallStatus.escalated if escalated else CallStatus.completed
    db.commit()
    return True


async def finalize(session_factory: sessionmaker, call_id: int, agent_outcome: CallOutcome | None = None,
                   note: str | None = None, agent: User | None = None) -> None:
    """Close the call once (idempotent), then run post-call analysis in the background."""

    def close() -> bool:
        with session_factory() as db:
            return _close(db, call_id, agent_outcome, note, agent.id if agent else None)

    if not await asyncio.to_thread(close):
        return
    SESSIONS.pop(call_id, None)

    async def analyse() -> None:
        with session_factory() as db:
            await analyse_call(db, call_id, agent_outcome)

    spawn(analyse())
