"""After a call: Gemini Flash writes summary/outcome/sentiment/next_action and a 5-point QA score.

Facts the system knows for certain (which tools ran, whether identity was verified, what the agent
chose) override the model, so the model can't claim a payment link that was never sent."""

import asyncio
import json
import logging
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.models import Call, QAScore, TranscriptTurn
from app.models.enums import CallOutcome, Sentiment, Speaker

log = logging.getLogger(__name__)


RETRY_DELAYS = (0, 15, 45)  # seconds before each analysis round


class QAChecks(BaseModel):
    ai_disclosure: bool = Field(description="Bot said it is an automated AI assistant AND that the call is recorded")
    identity_verified: bool = Field(description="verify_customer succeeded before any policy detail was shared")
    correct_figures: bool = Field(description="Every amount/date the bot spoke matches a tool result exactly")
    no_mis_selling: bool = Field(description="No pressure, no guarantees, no unrequested add-ons")
    polite_close: bool = Field(description="Bot summarised next steps and thanked the customer")


class Analysis(BaseModel):
    summary: str = Field(description="2-3 sentences, plain English, for a supervisor")
    outcome: Literal["payment_link_sent", "callback_scheduled", "escalated", "info_provided", "not_interested", "no_answer"]
    sentiment: Literal["positive", "neutral", "negative"]
    next_action: str = Field(description="One short instruction for the team")
    qa: QAChecks


PROMPT = """You are a quality analyst for an Indian insurer's AI contact centre.
Analyse this call transcript. TOOL lines are system records of tool calls and their results; they are
ground truth for figures. Judge only from the transcript.

Transcript:
{transcript}
"""


def _transcript(turns: list[TranscriptTurn]) -> str:
    lines = []
    for t in turns:
        if t.speaker == Speaker.tool:
            lines.append(f"TOOL {t.tool_name}: {json.dumps((t.tool_payload or {}).get('result', {}), ensure_ascii=False)}")
        else:
            lines.append(f"{t.speaker.upper()}: {t.text}")
    return "\n".join(lines)


def _facts(turns: list[TranscriptTurn]) -> set[str]:
    return {t.tool_name for t in turns if t.speaker == Speaker.tool and (t.tool_payload or {}).get("result", {}).get("ok")}


def decide_outcome(facts: set[str], agent_outcome: CallOutcome | None, model_outcome: str | None,
                   spoke: bool) -> CallOutcome:
    if agent_outcome:
        return agent_outcome
    for tool, outcome in (("escalate_to_human", CallOutcome.escalated),
                          ("send_payment_link", CallOutcome.payment_link_sent),
                          ("schedule_followup", CallOutcome.callback_scheduled)):
        if tool in facts:
            return outcome
    if not spoke:
        return CallOutcome.no_answer
    # The model may only choose outcomes that need no tool evidence.
    return CallOutcome(model_outcome) if model_outcome in ("info_provided", "not_interested") else CallOutcome.info_provided


async def _ask_gemini(transcript: str) -> Analysis:
    """Primary Flash model, then the fallback: Flash models return 503 under peak demand."""
    s = get_settings()
    client = genai.Client(api_key=s.google_api_key, http_options=types.HttpOptions(
        timeout=30_000, retry_options=types.HttpRetryOptions(attempts=1)))
    # Low thinking: a QA rubric needs no deep reasoning, and default thinking can take minutes under load.
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=Analysis,
                                         temperature=0.2, thinking_config=types.ThinkingConfig(thinking_level="LOW"))
    error: Exception | None = None
    models = [s.gemini_flash_model, *(m.strip() for m in s.gemini_flash_fallback_models.split(",") if m.strip())]
    for model in dict.fromkeys(models):
        try:
            resp = await client.aio.models.generate_content(
                model=model, contents=PROMPT.format(transcript=transcript), config=config)
            return Analysis.model_validate_json(resp.text)
        except Exception as e:
            log.warning("Gemini %s failed for post-call analysis: %s", model, e)
            error = e
    raise error


async def analyse_call(db: Session, call_id: int, agent_outcome: CallOutcome | None = None) -> Call:
    call = db.get(Call, call_id)
    turns = list(db.scalars(select(TranscriptTurn).where(TranscriptTurn.call_id == call_id).order_by(TranscriptTurn.offset_ms, TranscriptTurn.seq)))
    facts = _facts(turns)
    # Tools only run on customer input, so a successful tool call also proves a conversation happened.
    spoke = bool(facts) or any(t.speaker == Speaker.customer for t in turns)

    analysis: Analysis | None = None
    if get_settings().google_api_key and spoke:
        for delay in RETRY_DELAYS:  # demand spikes on Gemini are usually short; the UI shows "Analysing" meanwhile
            await asyncio.sleep(delay)
            try:
                analysis = await _ask_gemini(_transcript(turns))
                break
            except Exception:  # analysis must never lose the call record
                log.exception("Post-call analysis failed for call %s", call_id)

    call.outcome = decide_outcome(facts, agent_outcome, analysis.outcome if analysis else None, spoke)
    if analysis:
        call.summary, call.next_action = analysis.summary, analysis.next_action
        call.sentiment = Sentiment(analysis.sentiment)
        checks = analysis.qa.model_dump()
    else:
        call.summary = ("No customer speech was captured." if not spoke
                        else "Automatic summary unavailable. Review the transcript.")
        call.next_action = "Review the call manually."
        call.sentiment = Sentiment.neutral
        checks = None  # unknown is not "failed": leave QA unscored for a human
    if spoke and checks is not None:
        checks["identity_verified"] = call.verified  # system fact, not the model's opinion
        qa = db.get(QAScore, call_id) or QAScore(call_id=call_id)
        for k, v in checks.items():
            setattr(qa, k, v)
        qa.score, qa.source, qa.updated_at = sum(checks.values()), "ai", utcnow()
        db.add(qa)
    db.commit()
    return call
