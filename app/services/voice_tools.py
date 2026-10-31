"""The voice agent's five tools as plain DB services (no Pipecat/Gemini here, so they are unit-tested).

Guardrails live in code, not only in the prompt: nothing about a policy is returned until
verify_customer has succeeded for this call, and every figure the bot may speak comes from here.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.formatting import IST, format_date, format_inr
from app.core.masking import mask_phone
from app.models import Call, Customer, Escalation, Followup, Policy, TranscriptTurn
from app.models.enums import (
    EscalationStatus, FollowupKind, FollowupStatus, Priority, Speaker,
)

MAX_VERIFY_ATTEMPTS = 3
CALL_WINDOW = (9, 19)  # TRAI calling window, IST


@dataclass
class CallState:
    """Per-call guardrail state shared by the tools (lives in memory for the call's duration)."""

    call_id: int
    started_at: datetime
    verified_customer_id: int | None = None
    verify_attempts: int = 0
    actions: list[str] = field(default_factory=list)  # tool names that succeeded

    @property
    def verified(self) -> bool:
        return self.verified_customer_id is not None


# --- helpers -----------------------------------------------------------------

def normalize_policy_no(raw: str) -> str:
    """'mot 2026 1234' / 'MOT-2026-001234' -> 'MOT-2026-001234'."""
    m = re.search(r"([A-Za-z]{3})\D*(\d{4})\D*(\d{1,6})", raw or "")
    return f"{m[1].upper()}-{m[2]}-{int(m[3]):06d}" if m else (raw or "").strip().upper()


_DOB_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y")


def parse_dob(raw: str) -> date | None:
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", (raw or "").strip()).replace(",", "")
    for fmt in _DOB_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_when(raw: str, now: datetime) -> datetime:
    """ISO datetime from the model (IST if naive) -> aware datetime, moved into the calling window.
    Unparseable -> next working-hours slot at 11:00 IST."""
    try:
        when = datetime.fromisoformat((raw or "").strip())
        when = when.replace(tzinfo=IST) if when.tzinfo is None else when.astimezone(IST)
    except ValueError:
        when = datetime.combine(now.astimezone(IST).date() + timedelta(days=1), time(11, 0), IST)
    if when.hour < CALL_WINDOW[0]:
        when = when.replace(hour=CALL_WINDOW[0], minute=0)
    elif when.hour >= CALL_WINDOW[1]:
        when = datetime.combine(when.date() + timedelta(days=1), time(CALL_WINDOW[0], 0), IST)
    if when <= now:
        when = now.astimezone(IST) + timedelta(hours=1)
    return when


def add_turn(db: Session, state: CallState, speaker: Speaker, text: str,
             tool_name: str | None = None, payload: dict | None = None, at: datetime | None = None) -> TranscriptTurn:
    """Append a transcript turn. `at` = when the turn started (defaults to now); turns are read in offset order."""
    seq = (db.scalar(select(func.max(TranscriptTurn.seq)).where(TranscriptTurn.call_id == state.call_id)) or 0) + 1
    turn = TranscriptTurn(call_id=state.call_id, seq=seq, speaker=speaker, text=text,
                          offset_ms=max(int(((at or utcnow()) - state.started_at).total_seconds() * 1000), 0),
                          tool_name=tool_name, tool_payload=payload)
    db.add(turn)
    return turn


def _policy(db: Session, policy_no: str) -> Policy | None:
    return db.scalar(select(Policy).where(Policy.policy_no == normalize_policy_no(policy_no)))


def _verified_policy(db: Session, state: CallState, policy_no: str) -> tuple[Policy | None, dict | None]:
    """The policy, only if the caller is verified and it belongs to them; else an error result."""
    if not state.verified:
        return None, {"ok": False, "error": "Customer is not verified. Ask for the policy number and date of "
                                            "birth and call verify_customer first. Share no policy details."}
    p = _policy(db, policy_no)
    if not p or p.customer_id != state.verified_customer_id:
        return None, {"ok": False, "error": "That policy number is not on this customer's record."}
    return p, None


def _record(db: Session, state: CallState, tool: str, text: str, args: dict, result: dict) -> dict:
    add_turn(db, state, Speaker.tool, text, tool, {"args": args, "result": result})
    if result.get("ok"):
        state.actions.append(tool)
    db.commit()
    return result


# --- the five tools ------------------------------------------------------------

def verify_customer(db: Session, state: CallState, policy_no: str, dob: str) -> dict[str, Any]:
    args = {"policy_no": policy_no, "dob": "provided"}  # never store the spoken DOB in the transcript payload
    call = db.get(Call, state.call_id)
    if state.verified:
        return {"ok": True, "message": "Customer is already verified."}
    if state.verify_attempts >= MAX_VERIFY_ATTEMPTS:
        return _record(db, state, "verify_customer", "Verification locked after 3 attempts", args,
                       {"ok": False, "error": "Too many failed attempts. Offer to connect a person with escalate_to_human."})
    state.verify_attempts += 1
    p = _policy(db, policy_no)
    given = parse_dob(dob)
    customer = db.get(Customer, p.customer_id) if p else None
    # Renewal calls are placed to a known customer: the policy must be theirs.
    matches = (customer is not None and given == customer.dob
               and (call.customer_id is None or call.customer_id == customer.id))
    if not matches:
        left = MAX_VERIFY_ATTEMPTS - state.verify_attempts
        return _record(db, state, "verify_customer", "Verification failed", args,
                       {"ok": False, "error": f"Details did not match our records. {left} attempt(s) left. "
                                              "Do not say which detail was wrong."})
    state.verified_customer_id = customer.id
    call.customer_id, call.policy_id, call.verified = customer.id, p.id, True
    return _record(db, state, "verify_customer", f"Customer verified: {customer.full_name}", args,
                   {"ok": True, "customer_name": customer.full_name, "preferred_language": customer.preferred_language,
                    "policy_no": p.policy_no})


def get_policy_details(db: Session, state: CallState, policy_no: str) -> dict[str, Any]:
    args = {"policy_no": policy_no}
    p, err = _verified_policy(db, state, policy_no)
    if err:
        return _record(db, state, "get_policy_details", "Policy details refused: not verified", args, err)
    days = (p.renewal_date - utcnow().astimezone(IST).date()).days
    return _record(db, state, "get_policy_details", f"Policy details fetched: {p.policy_no}", args, {
        "ok": True, "policy_no": p.policy_no, "product": p.product, "plan_name": p.plan_name,
        "status": p.status, "premium_inr": p.premium_inr, "premium_spoken": format_inr(p.premium_inr),
        "sum_insured_inr": p.sum_insured_inr, "sum_insured_spoken": format_inr(p.sum_insured_inr),
        "renewal_date": p.renewal_date.isoformat(), "renewal_date_spoken": format_date(p.renewal_date),
        "days_to_renewal": days, "vehicle_registration": p.vehicle_reg,
    })


def send_payment_link(db: Session, state: CallState, policy_no: str, created_by: int | None = None) -> dict[str, Any]:
    args = {"policy_no": policy_no}
    p, err = _verified_policy(db, state, policy_no)
    if err:
        return _record(db, state, "send_payment_link", "Payment link refused: not verified", args, err)
    customer = db.get(Customer, p.customer_id)
    url = f"{get_settings().payment_link_base}/{p.policy_no.lower()}-{state.call_id}"
    first = customer.full_name.split()[0]
    db.add(Followup(policy_id=p.id, customer_id=customer.id, call_id=state.call_id, kind=FollowupKind.payment_link,
                    due_at=utcnow(), reason="Payment link requested on call", status=FollowupStatus.sent,
                    message_text=f"Hi {first}, here is your secure link to renew {p.policy_no} "
                                 f"({format_inr(p.premium_inr)}, due {format_date(p.renewal_date)}): {url} "
                                 "– AssureVoice Insurance",
                    payment_link_url=url, created_by=created_by, sent_at=utcnow()))
    return _record(db, state, "send_payment_link", f"Payment link sent on WhatsApp for {p.policy_no}", args, {
        "ok": True, "sent_to": f"WhatsApp {mask_phone(customer.phone)}", "amount_spoken": format_inr(p.premium_inr),
        "note": "Sandbox link; no real message or payment." if get_settings().app_env == "sandbox" else
                "Pilot – link not payable and WhatsApp is not live: tell the customer a colleague will share the payment link.",
    })


def schedule_followup(db: Session, state: CallState, policy_no: str, when: str, reason: str,
                      created_by: int | None = None) -> dict[str, Any]:
    args = {"policy_no": policy_no, "when": when, "reason": reason}
    p, err = _verified_policy(db, state, policy_no)
    if err:
        return _record(db, state, "schedule_followup", "Callback refused: not verified", args, err)
    due = parse_when(when, utcnow())
    db.add(Followup(policy_id=p.id, customer_id=p.customer_id, call_id=state.call_id, kind=FollowupKind.callback,
                    due_at=due, reason=(reason or "Customer requested a callback")[:500],
                    status=FollowupStatus.scheduled, created_by=created_by))
    spoken = f"{format_date(due)} at {due.astimezone(IST):%I:%M %p} IST"
    return _record(db, state, "schedule_followup", f"Callback scheduled for {spoken}", args,
                   {"ok": True, "scheduled_for": due.isoformat(), "scheduled_for_spoken": spoken})


def escalate_to_human(db: Session, state: CallState, policy_no: str, reason: str) -> dict[str, Any]:
    args = {"policy_no": policy_no, "reason": reason}
    call = db.get(Call, state.call_id)
    p = _policy(db, policy_no) if policy_no else None
    if p and state.verified and p.customer_id != state.verified_customer_id:
        p = None
    customer_id = state.verified_customer_id or call.customer_id or (p.customer_id if p and state.verified else None)
    if customer_id is None:
        # Unverified inbound caller: the escalation still reaches a person, linked to the call only.
        result = {"ok": True, "message": "A service executive will join this call now."}
    else:
        db.add(Escalation(call_id=call.id, customer_id=customer_id, policy_id=(p.id if p else call.policy_id),
                          reason=(reason or "Customer asked for a person")[:500], priority=Priority.high,
                          status=EscalationStatus.open))
        result = {"ok": True, "message": "Escalation raised. A service executive will join this call now."}
    return _record(db, state, "escalate_to_human", f"Escalated to a human agent: {reason}", args, result)
