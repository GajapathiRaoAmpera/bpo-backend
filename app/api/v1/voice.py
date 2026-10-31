from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate, SmallWebRTCPatchRequest, SmallWebRTCRequest,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db, utcnow
from app.core.formatting import IST
from app.core.rbac import Access, Level, Screen, require
from app.models import Call, Customer, Policy, QAScore, TranscriptTurn, User
from app.models.enums import AuditAction, CallDirection, CallMode, CallStatus, PolicyStatus, Speaker
from app.schemas.customer import CustomerOut, PolicyOut, customer_fields
from app.schemas.voice import (
    EndIn, IcePatchIn, OfferIn, QAOut, QueueItem, SessionIn, SessionOut, TurnOut,
)
from app.services.scripts import active_script
from app.services.voice_tools import CallState, add_turn
from app.voice.session import SESSIONS, LiveSession, finalize, spawn, stop_bot

router = APIRouter(prefix="/voice", tags=["voice"])
console = require(Screen.agent_console, Level.full)


def _owned_live_call(db: Session, call_id: int, user: User) -> Call:
    call = db.get(Call, call_id)
    if not call or call.agent_user_id != user.id:
        raise HTTPException(404, "Call not found")
    if call.ended_at is not None:
        raise HTTPException(409, "This call has already ended")
    return call


def _session_out(db: Session, call: Call) -> SessionOut:
    live = SESSIONS.get(call.id)
    customer = db.get(Customer, call.customer_id) if call.customer_id else None
    policy = db.get(Policy, call.policy_id) if call.policy_id else None
    turns = db.scalars(select(TranscriptTurn).where(TranscriptTurn.call_id == call.id).order_by(TranscriptTurn.offset_ms, TranscriptTurn.seq))
    qa = db.get(QAScore, call.id)
    return SessionOut(
        call_id=call.id, call_ref=call.call_ref, mode=call.mode, status=call.status, started_at=call.started_at,
        ended_at=call.ended_at, duration_sec=call.duration_sec, verified=call.verified,
        live=bool(live and live.runner), taken_over=call.taken_over_at is not None,
        analysis_pending=call.ended_at is not None and call.summary is None,
        outcome=call.outcome, sentiment=call.sentiment, summary=call.summary, next_action=call.next_action,
        customer=CustomerOut(**customer_fields(customer, masked=False)) if customer else None,
        policy=PolicyOut.model_validate(policy) if policy else None,
        turns=[TurnOut.model_validate(t) for t in turns], qa=QAOut.model_validate(qa) if qa else None,
    )


@router.get("/queue", response_model=list[QueueItem])
def renewal_queue(access: Access = Depends(console), db: Session = Depends(get_db)) -> list[QueueItem]:
    """Policies due for renewal soonest, for the console's call list."""
    today = utcnow().astimezone(IST).date()
    last_call = (select(Call.policy_id, func.max(Call.started_at).label("last")).group_by(Call.policy_id).subquery())
    rows = db.execute(
        select(Policy, Customer, last_call.c.last)
        .join(Customer, Customer.id == Policy.customer_id)
        .outerjoin(last_call, last_call.c.policy_id == Policy.id)
        .where(Policy.status.in_([PolicyStatus.due, PolicyStatus.active]),
               Policy.renewal_date.between(today, today + timedelta(days=30)))
        .order_by(Policy.renewal_date, Policy.id).limit(15)
    ).all()
    return [QueueItem(policy_id=p.id, policy_no=p.policy_no, product=p.product, plan_name=p.plan_name,
                      premium_inr=p.premium_inr, renewal_date=p.renewal_date, customer_id=c.id,
                      customer_name=c.full_name, city=c.city, preferred_language=c.preferred_language,
                      last_called_at=last) for p, c, last in rows]


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionIn, request: Request, access: Access = Depends(console),
                         db: Session = Depends(get_db)) -> SessionOut:
    script = active_script(db, body.mode)
    if script is None:
        raise HTTPException(409, f"No approved {body.mode} script. A script must be approved before calls can run.")
    policy = None
    if body.mode == CallMode.renewal:
        policy = db.get(Policy, body.policy_id) if body.policy_id else None
        if policy is None:
            raise HTTPException(422, "Choose a policy for a renewal call")
    for cid, other in list(SESSIONS.items()):
        if other.agent_user_id != access.user.id:
            continue
        if other.runner is not None or other.taken_over:
            raise HTTPException(409, "You already have a call in progress. End it before starting another.")
        await finalize(request.app.state.session_factory, cid)  # never connected (e.g. tab closed)
    now = utcnow()
    day_count = db.scalar(select(func.count()).select_from(Call).where(
        Call.call_ref.like(f"CALL-{now.astimezone(IST):%Y%m%d}-%"))) or 0
    call = Call(call_ref=f"CALL-{now.astimezone(IST):%Y%m%d}-{day_count + 1:04d}",
                direction=CallDirection.outbound if policy else CallDirection.inbound, mode=body.mode,
                customer_id=policy.customer_id if policy else None, policy_id=policy.id if policy else None,
                agent_user_id=access.user.id, script_id=script.id, status=CallStatus.in_progress,
                started_at=now, is_simulated=False)
    db.add(call)
    db.flush()
    SESSIONS[call.id] = LiveSession(state=CallState(call_id=call.id, started_at=now), agent_user_id=access.user.id)
    audit.record(db, request, AuditAction.call_start, entity_type="call", entity_id=call.id,
                 detail={"call_ref": call.call_ref, "mode": body.mode, "script_version": script.version})
    return _session_out(db, call)


@router.get("/sessions/{call_id}", response_model=SessionOut)
def get_session(call_id: int, access: Access = Depends(console), db: Session = Depends(get_db)) -> SessionOut:
    call = db.get(Call, call_id)
    if not call or (call.agent_user_id != access.user.id and access.user.role != "team_lead"):
        raise HTTPException(404, "Call not found")
    return _session_out(db, call)


@router.post("/sessions/{call_id}/offer")
async def offer(call_id: int, body: OfferIn, request: Request, access: Access = Depends(console),
                db: Session = Depends(get_db)) -> dict:
    """WebRTC signalling (Pipecat SmallWebRTC): SDP offer in, answer out; starts the bot on first offer."""
    _owned_live_call(db, call_id, access.user)
    live = SESSIONS.get(call_id)
    if live is None:
        raise HTTPException(409, "This call can no longer be connected")
    if live.runner is not None and not body.pc_id:
        raise HTTPException(409, "This call is already connected")
    # heavy imports (pipecat/torch VAD) only when a call starts
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    from app.voice.bot import run_bot

    factory = request.app.state.session_factory

    async def start(connection) -> None:
        transport = SmallWebRTCTransport(webrtc_connection=connection,
                                         params=TransportParams(audio_in_enabled=True, audio_out_enabled=True))
        spawn(run_bot(transport, live, factory))

    return await request.app.state.webrtc.handle_web_request(
        SmallWebRTCRequest(sdp=body.sdp, type=body.type, pc_id=body.pc_id, restart_pc=body.restart_pc,
                           request_data=body.request_data), start)


@router.patch("/sessions/{call_id}/offer")
async def ice_candidates(call_id: int, body: IcePatchIn, request: Request, access: Access = Depends(console),
                         db: Session = Depends(get_db)) -> dict:
    _owned_live_call(db, call_id, access.user)
    await request.app.state.webrtc.handle_patch_request(SmallWebRTCPatchRequest(
        pc_id=body.pc_id, candidates=[IceCandidate(**c.model_dump()) for c in body.candidates]))
    request.state.audited = True  # signalling noise, not a business change
    return {"status": "ok"}


@router.post("/sessions/{call_id}/takeover", response_model=SessionOut)
async def takeover(call_id: int, request: Request, access: Access = Depends(console),
                   db: Session = Depends(get_db)) -> SessionOut:
    call = _owned_live_call(db, call_id, access.user)
    live = SESSIONS.get(call_id)
    if call.taken_over_at is None:
        call.taken_over_at = utcnow()
        if live:
            live.taken_over = True
            add_turn(db, live.state, Speaker.agent,
                     f"{access.user.full_name} ({access.user.job_title}) took over the call from the AI assistant.")
        db.commit()
        audit.record(db, request, AuditAction.takeover, entity_type="call", entity_id=call.id,
                     detail={"call_ref": call.call_ref})
    if live:
        await stop_bot(live, {"type": "takeover", "by": access.user.full_name})
    db.refresh(call)
    return _session_out(db, call)


@router.post("/sessions/{call_id}/end", response_model=SessionOut)
async def end_session(call_id: int, body: EndIn, request: Request, access: Access = Depends(console),
                      db: Session = Depends(get_db)) -> SessionOut:
    call = _owned_live_call(db, call_id, access.user)
    if body.outcome and call.taken_over_at is None:
        raise HTTPException(422, "Only a human agent who took over the call can set its outcome")
    live = SESSIONS.get(call_id)
    if live:
        live.taken_over = True  # we finalise here, not in the bot's shutdown path
        await stop_bot(live)
    await finalize(request.app.state.session_factory, call_id, body.outcome, body.note, access.user)
    audit.record(db, request, AuditAction.call_end, entity_type="call", entity_id=call_id,
                 detail={"call_ref": call.call_ref, "agent_outcome": body.outcome})
    db.expire_all()
    return _session_out(db, db.get(Call, call_id))
