"""Pipecat bot: browser (SmallWebRTC) or phone (Plivo WebSocket) <-> Gemini Live, with the five policy tools.

Pattern follows the Pipecat 1.15 realtime template (PipelineWorker + WorkerRunner + RTVI)."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.formatting import IST, format_date
from app.models import Call, Customer, Policy, Script
from app.models.enums import CallMode, Language, Speaker
from app.services import voice_tools
from app.voice.session import LiveSession, finalize

log = logging.getLogger(__name__)

LANG_CODE = {Language.english: "en-IN", Language.tamil: "ta-IN", Language.hindi: "hi-IN"}

_POLICY = {"type": "string", "description": "Policy number as the customer said it, e.g. MOT-2026-001234"}
TOOL_SPECS: list[tuple[str, str, dict, list[str]]] = [
    ("verify_customer", "Verify the caller's identity. Required before sharing ANY policy information.",
     {"policy_no": _POLICY, "dob": {"type": "string", "description": "Date of birth as YYYY-MM-DD"}},
     ["policy_no", "dob"]),
    ("get_policy_details", "Get plan, status, premium, sum insured and renewal date for a verified customer's policy.",
     {"policy_no": _POLICY}, ["policy_no"]),
    ("send_payment_link", "Send a secure renewal payment link to the customer's registered WhatsApp number.",
     {"policy_no": _POLICY}, ["policy_no"]),
    ("schedule_followup", "Schedule a callback at the customer's preferred time.",
     {"policy_no": _POLICY,
      "when": {"type": "string", "description": "ISO 8601 datetime in IST, e.g. 2026-10-30T18:00:00+05:30"},
      "reason": {"type": "string", "description": "Why the customer wants a callback"}},
     ["policy_no", "when", "reason"]),
    ("escalate_to_human", "Hand the call to a human service executive.",
     {"policy_no": {**_POLICY, "description": "Policy number if known, else empty string"},
      "reason": {"type": "string", "description": "Short reason for escalation"}},
     ["policy_no", "reason"]),
]


def build_prompt(script: Script, call: Call, customer: Customer | None, policy: Policy | None) -> str:
    today = utcnow().astimezone(IST)
    if call.mode == CallMode.renewal and customer and policy:
        context = (f"- This is an outbound renewal call to {customer.full_name} about policy {policy.policy_no}.\n"
                   f"- Treat the name and policy as unverified context: confirm who you are speaking to and verify "
                   f"before sharing anything. Preferred language: {customer.preferred_language}.")
    else:
        context = "- This is an inbound call. The caller's identity is unknown until verify_customer succeeds."
    return (f"{script.system_prompt}\n\nCall context:\n{context}\n"
            f"- Today is {format_date(today)}, {today:%H:%M} IST. Calling hours are 09:00 to 19:00 IST.\n\n"
            "- The disclosure (automated AI assistant + call is recorded) must be completed. If you are "
            "interrupted before finishing it, complete it at the start of your next reply.\n\n"
            "Tool rules:\n- Pass dates of birth as YYYY-MM-DD and callback times as ISO 8601 in IST.\n"
            "- When stating money or dates, use the *_spoken values from tool results exactly.\n"
            "- If a tool returns ok=false, follow its error text.\n"
            "- Keep replies short; this is a phone call.")


def _load(session_factory: sessionmaker, call_id: int):
    with session_factory() as db:
        call = db.get(Call, call_id)
        script = db.get(Script, call.script_id)
        customer = db.get(Customer, call.customer_id) if call.customer_id else None
        policy = db.get(Policy, call.policy_id) if call.policy_id else None
        db.expunge_all()
        return call, script, customer, policy


async def run_bot(transport: BaseTransport, live: LiveSession, session_factory: sessionmaker,
                  phone: bool = False) -> None:
    s = get_settings()
    state = live.state
    call, script, customer, policy = await asyncio.to_thread(_load, session_factory, state.call_id)

    worker: PipelineWorker | None = None

    async def notify(data: dict) -> None:
        if worker is not None:
            await worker.rtvi.send_server_message(data)

    def db_call(fn: Callable, **kwargs) -> dict:
        with session_factory() as db:
            return fn(db, state, **kwargs)

    def make_handler(name: str):
        fn = getattr(voice_tools, name)

        async def handler(params: FunctionCallParams) -> None:
            args = {k: str(v) for k, v in (params.arguments or {}).items()}
            try:
                result = await asyncio.to_thread(db_call, fn, **args)
            except TypeError:
                result = {"ok": False, "error": "Missing or invalid arguments for this tool."}
            await params.result_callback(result)
            await notify({"type": "action", "tool": name, "ok": bool(result.get("ok")), "result": result,
                          "at": utcnow().isoformat()})
            if name == "verify_customer" and result.get("ok"):
                await notify({"type": "verified", "customer_id": state.verified_customer_id})
            if name == "escalate_to_human" and result.get("ok"):
                await notify({"type": "escalation_requested", "reason": args.get("reason", "")})

        return handler

    tools = [FunctionSchema(name=n, description=d, properties=p, required=r, handler=make_handler(n))
             for n, d, p, r in TOOL_SPECS]
    language = LANG_CODE.get(customer.preferred_language, "en-IN") if customer else "en-IN"
    llm = GeminiLiveLLMService(
        api_key=s.google_api_key,
        settings=GeminiLiveLLMService.Settings(model=s.gemini_live_model, voice=s.gemini_live_voice,
                                               system_instruction=build_prompt(script, call, customer, policy),
                                               language=language),
        tools=tools,
    )
    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()))

    def save_turn(speaker: Speaker, text: str, at: datetime | None = None) -> None:
        with session_factory() as db:
            voice_tools.add_turn(db, state, speaker, text, at=at)
            db.commit()

    def started(timestamp: str | None) -> datetime | None:
        try:
            ts = datetime.fromisoformat(timestamp) if timestamp else None
            return ts if ts and ts.tzinfo else None
        except ValueError:
            return None

    @user_agg.event_handler("on_user_turn_message_added")
    async def _user_turn(_agg, message) -> None:
        # Realtime mode finalises the user's words when the bot starts replying (after any tool
        # calls), so record when the user started speaking to keep the transcript in order.
        if message.content and message.content.strip():
            await asyncio.to_thread(save_turn, Speaker.customer, message.content.strip(), started(message.timestamp))

    @assistant_agg.event_handler("on_assistant_turn_stopped")
    async def _bot_turn(_agg, message) -> None:
        if message.content and message.content.strip():
            await asyncio.to_thread(save_turn, Speaker.bot, message.content.strip())

    pipeline = Pipeline([transport.input(), user_agg, llm, transport.output(), assistant_agg])
    worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=False))
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    live.runner, live.worker = runner, worker

    # A phone line has no RTVI client, so start talking as soon as Plivo's audio stream connects.
    source, event = (transport, "on_client_connected") if phone else (worker.rtvi, "on_client_ready")

    @source.event_handler(event)
    async def _ready(*_) -> None:
        context.add_message({"role": "developer",
                             "content": "The call has connected. Begin now with your opening and the disclosure."})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def _gone(_transport, _client) -> None:
        await runner.cancel()

    try:
        await runner.run()
    except Exception:
        log.exception("Voice bot crashed for call %s", state.call_id)
    finally:
        live.runner = live.worker = None
        if not live.taken_over:  # after takeover the human agent ends the call with an outcome
            await finalize(session_factory, state.call_id)
