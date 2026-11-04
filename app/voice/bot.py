"""Pipecat bot: browser (SmallWebRTC) or phone (Plivo WebSocket) <-> Gemini Live, with the five policy tools.

Pattern follows the Pipecat 1.15 realtime template (PipelineWorker + WorkerRunner + RTVI)."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
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
from app.voice.session import LiveSession, finalize, spawn

log = logging.getLogger(__name__)

LANG_CODE = {Language.english: "en-IN", Language.tamil: "ta-IN", Language.hindi: "hi-IN",
             Language.telugu: "te-IN", Language.kannada: "kn-IN", Language.malayalam: "ml-IN"}
LANGUAGES_SPOKEN = ", ".join(lang.title() for lang in Language)
IDLE_SECS = 7  # silence after the bot stops talking before it re-prompts
IDLE_REPROMPTS = 2  # then one goodbye, then the call ends
NO_REPLY_SECS = 5  # customer finished speaking but the bot hasn't started replying: nudge it
# shortcut: fixed pause so the bot's "an executive will join" line plays before the transfer cuts its audio;
# upgrade to waiting for the bot's turn to end if the line gets clipped.
TRANSFER_DELAY_SECS = 6

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
    ("set_language", "Record the language the customer wants to continue in.",
     {"language": {"type": "string", "enum": [lang.value for lang in Language]}}, ["language"]),
    ("register_claim", "Register an insurance claim for a verified customer's policy. Returns the claim number.",
     {"policy_no": _POLICY,
      "incident_date": {"type": "string", "description": "When the incident happened, YYYY-MM-DD"},
      "description": {"type": "string", "description": "What happened and where, in the customer's words"}},
     ["policy_no", "incident_date", "description"]),
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
            "- After calling a tool, say a short filler such as 'one moment, let me check' and do not state any "
            "result or figure until the tool result arrives.\n"
            "- Keep replies short; this is a phone call.\n\n"
            "Language:\n- Right after the disclosure, ask which language they would like to continue in: "
            f"{LANGUAGES_SPOKEN}. Continue in the one they pick and call set_language. If they switch "
            "language later, switch immediately and call set_language again.\n\n"
            "Interruptions and silence:\n- If the customer interrupts, stop, answer what they just said, and do "
            "not restart your previous sentence.\n- If you only heard noise or a short 'ok' or 'hmm', continue "
            "from where you stopped.\n- Never stay silent: every customer turn gets a reply.")


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
            turns["tools"] += 1
            try:
                result = await asyncio.to_thread(db_call, fn, **args)
            except TypeError:
                result = {"ok": False, "error": "Missing or invalid arguments for this tool."}
            finally:
                turns["tools"] -= 1
            await params.result_callback(result)
            await notify({"type": "action", "tool": name, "ok": bool(result.get("ok")), "result": result,
                          "at": utcnow().isoformat()})
            if name == "verify_customer" and result.get("ok"):
                await notify({"type": "verified", "customer_id": state.verified_customer_id})
            if name == "escalate_to_human" and result.get("ok"):
                await notify({"type": "escalation_requested", "reason": args.get("reason", "")})
                if phone and live.plivo_uuid and s.agent_transfer_number:
                    spawn(transfer())

        return handler

    async def transfer() -> None:
        from app.api.v1.telephony import transfer_to_agent  # api layer imports this module

        await asyncio.sleep(TRANSFER_DELAY_SECS)
        try:
            await asyncio.to_thread(transfer_to_agent, state.call_id, live.plivo_uuid)
        except Exception:  # the Escalation row stays open for a person to call back
            log.exception("Plivo transfer to the agent line failed for call %s", state.call_id)

    turns = {"user": 0, "bot": 0, "tools": 0}  # counters for the no-reply check
    tools = [FunctionSchema(name=n, description=d, properties=p, required=r) for n, d, p, r in TOOL_SPECS]
    language = LANG_CODE.get(customer.preferred_language, "en-IN") if customer else "en-IN"
    llm = GeminiLiveLLMService(
        api_key=s.google_api_key,
        settings=GeminiLiveLLMService.Settings(model=s.gemini_live_model, voice=s.gemini_live_voice,
                                               system_instruction=build_prompt(script, call, customer, policy),
                                               language=language),
        tools=tools,
    )
    # Registered before the call connects, so Gemini is told these tools are NON_BLOCKING. A tool the
    # customer talks over then still finishes; with the default (cancel on interruption) Gemini waited
    # for a function response that never came. Registering after connect (FunctionSchema handlers)
    # declared them BLOCKING while pipecat treated them as async, and the call froze.
    for name, *_ in TOOL_SPECS:
        llm.register_function(name, make_handler(name), cancel_on_interruption=False, timeout_secs=20)
    context = LLMContext()
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(confidence=0.75, start_secs=0.3, min_volume=0.7)
                                           if phone else None),
            user_idle_timeout=IDLE_SECS))

    @user_agg.event_handler("on_user_turn_idle")
    async def _idle(_agg) -> None:
        # Safety net for any lost turn: re-prompt twice, then say goodbye, then end the call.
        state.idle_prompts += 1
        if state.idle_prompts > IDLE_REPROMPTS + 1:
            await runner.cancel()
            return
        note = ("The customer has gone quiet or may not have heard you. Briefly repeat your last question, or "
                "ask if they are still there, in the language you are speaking."
                if state.idle_prompts <= IDLE_REPROMPTS else
                "The customer is still silent. Say you will end the call now, that they can call back any time, "
                "thank them, and say goodbye. Do not ask anything else.")
        context.add_message({"role": "developer", "content": note})
        await worker.queue_frames([LLMRunFrame()])

    @user_agg.event_handler("on_user_turn_started")
    async def _user_started(*_) -> None:
        turns["user"] += 1

    @assistant_agg.event_handler("on_assistant_turn_started")
    async def _bot_started(*_) -> None:
        turns["bot"] += 1

    @user_agg.event_handler("on_user_turn_stopped")
    async def _user_stopped(*_) -> None:
        # The customer finished speaking. If the bot neither replies nor runs a tool within a few
        # seconds (and the customer hasn't spoken again), the turn was lost: ask the model to answer.
        seen = (turns["user"], turns["bot"])
        await asyncio.sleep(NO_REPLY_SECS)
        if (turns["user"], turns["bot"]) == seen and not turns["tools"] and worker is not None:
            log.warning("Call %s: no reply %ss after the customer spoke, nudging the bot", state.call_id, NO_REPLY_SECS)
            context.add_message({"role": "developer", "content":
                                 "The customer just spoke and you have not replied. Reply now to what they "
                                 "said; if you did not catch it, ask them to repeat."})
            await worker.queue_frames([LLMRunFrame()])

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
        state.idle_prompts = 0  # they spoke: the silence count starts over
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
