"""Plivo callbacks for real campaign calls.

Plivo has no user session, so these routes skip require(). Instead the answer and hangup webhooks must
carry a valid X-Plivo-Signature-V3, and the audio WebSocket must present the one-time token that the
signed answer webhook put in its URL. Anything else is rejected."""

import secrets
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from plivo import plivoxml
from plivo.utils.signature_v3 import validate_v3_signature
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models import Call, Campaign, CampaignTarget
from app.models.enums import TargetStatus
from app.services.voice_tools import CallState
from app.voice.session import SESSIONS, LiveSession, finalize, spawn, stop_bot

router = APIRouter(prefix="/telephony/plivo", tags=["telephony"])

# shortcut: in-process like SESSIONS, so one API worker process only; move with SESSIONS to scale out.
_STREAM_TOKENS: dict[int, str] = {}


async def _verified(request: Request) -> None:
    s = get_settings()
    sig = request.headers.get("X-Plivo-Signature-V3", "")
    nonce = request.headers.get("X-Plivo-Signature-V3-Nonce", "")
    params = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))  # Plivo posts urlencoded
    # Sign against the public URL Plivo called, not request.url (http/host differ behind Cloud Run).
    url = f"{s.public_base_url}{request.url.path}"
    if not (s.plivo_enabled and sig and nonce
            and validate_v3_signature("POST", url, nonce, s.plivo_auth_token, sig, params)):
        raise HTTPException(403, "Invalid Plivo signature")


def _phone_call(db: Session, call_id: int) -> Call:
    call = db.get(Call, call_id)
    if not call or call.is_simulated or call.ended_at is not None:
        raise HTTPException(404, "Call not found")
    return call


@router.post("/answer/{call_id}")
async def answer(call_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    """Customer picked up: tell Plivo to stream the call's audio to our WebSocket."""
    await _verified(request)
    call = _phone_call(db, call_id)
    if call.campaign_id and call_id not in SESSIONS:  # count each answered call once
        db.get(Campaign, call.campaign_id).connected += 1
        db.commit()
    SESSIONS.setdefault(call_id, LiveSession(state=CallState(call_id=call_id, started_at=call.started_at),
                                             agent_user_id=None))
    token = _STREAM_TOKENS[call_id] = secrets.token_urlsafe(24)
    base = get_settings().public_base_url.replace("https://", "wss://", 1)
    xml = plivoxml.ResponseElement().add(plivoxml.StreamElement(
        f"{base}/api/v1/telephony/plivo/ws/{call_id}/{token}", bidirectional=True, keepCallAlive=True,
        contentType="audio/x-mulaw;rate=8000"))
    return Response(xml.to_string(), media_type="application/xml")


@router.websocket("/ws/{call_id}/{token}")
async def stream(websocket: WebSocket, call_id: int, token: str) -> None:
    expected = _STREAM_TOKENS.pop(call_id, None)  # one use only
    live = SESSIONS.get(call_id)
    if not (expected and secrets.compare_digest(expected, token) and live and live.runner is None):
        await websocket.close(code=1008)
        return
    # heavy imports (pipecat/torch VAD) only when a call connects
    from pipecat.runner.utils import parse_telephony_websocket
    from pipecat.serializers.plivo import PlivoFrameSerializer
    from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

    from app.voice.bot import run_bot

    await websocket.accept()
    kind, data = await parse_telephony_websocket(websocket)  # reads Plivo's "start" event
    if kind != "plivo":
        await websocket.close(code=1008)
        return
    s = get_settings()
    transport = FastAPIWebsocketTransport(websocket=websocket, params=FastAPIWebsocketParams(
        audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False,
        serializer=PlivoFrameSerializer(stream_id=data["stream_id"], call_id=data["call_id"],
                                        auth_id=s.plivo_auth_id, auth_token=s.plivo_auth_token)))
    await run_bot(transport, live, websocket.app.state.session_factory, phone=True)


@router.post("/hangup/{call_id}")
async def hangup(call_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """Call ended (or was never answered). Stopping the bot finalizes the call; otherwise close it here."""
    await _verified(request)
    call = db.get(Call, call_id)
    if not call or call.is_simulated:
        raise HTTPException(404, "Call not found")
    _STREAM_TOKENS.pop(call_id, None)
    live = SESSIONS.get(call_id)
    if live and live.runner is not None:
        spawn(stop_bot(live))  # run_bot's cleanup finalizes the call
        return {"ok": True}
    if live is None:  # never answered: no-answer/busy; the next launch retries the target
        target = db.scalar(select(CampaignTarget).where(CampaignTarget.call_id == call_id))
        if target:
            target.status = TargetStatus.failed
            db.commit()
    await finalize(request.app.state.session_factory, call_id)  # idempotent; analysis marks no_answer
    return {"ok": True}
