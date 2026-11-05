"""Follow-up timer: every minute, scheduled follow-ups that are due are marked `sent` with a WhatsApp-style
message. Callbacks get theirs CALLBACK_HEADS_UP early ("we'll call you shortly"), then at their due time the
bot rings the customer through Plivo and the callback becomes `done` (dial_due_callbacks).
The message is always stored on the follow-up; it is also really sent over WhatsApp when
`whatsapp_enabled` and the customer passes CONTACT_ALLOWLIST_PREFIX (see app.core.config), otherwise it stays
sandbox-only (stored, never delivered)."""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from google import genai
from google.genai import types
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.formatting import format_date, format_inr
from app.models import Customer, Followup, Policy
from app.models.enums import CallMode, CallStatus, FollowupKind, FollowupStatus
from app.services.campaigns import place_call
from app.services.payments import payment_link_for
from app.services.scripts import active_script
from app.services.whatsapp import send_template

log = logging.getLogger(__name__)

INTERVAL_MINUTES = 1  # callbacks are rung within a minute of the promised time
CALLBACK_HEADS_UP = timedelta(minutes=5)
# Callbacks missed by more than this (server was down) are left to a human instead of ringing late.
CALLBACK_DIAL_WINDOW = timedelta(minutes=30)
BATCH = 25  # keeps one run short even after a long downtime
AI_FRESHNESS = timedelta(minutes=30)

PROMPT = """Write one short WhatsApp message (max 320 characters, plain text, no emojis) from Ampera BPO
Insurance to a policyholder. Be polite and factual; use only the facts below; no promises or offers.
End with "– Ampera BPO".
Facts: first name {first}; policy {policy_no} ({plan}); premium {premium}; renewal due {due};
purpose: {purpose}; note from the team: {reason}{link}"""

PURPOSE = {
    FollowupKind.callback: "confirm the callback the customer asked for",
    FollowupKind.reminder: "remind them that the renewal is due",
    FollowupKind.payment_link: "share the secure payment link to renew",
}


def template_message(f: Followup, c: Customer, p: Policy) -> str:
    first = c.full_name.split()[0]
    facts = f"{p.policy_no} ({format_inr(p.premium_inr)}, due {format_date(p.renewal_date)})"
    if f.kind == FollowupKind.payment_link:
        body = f"here is your secure link to renew {facts}: {f.payment_link_url}"
    elif f.kind == FollowupKind.callback:
        body = f"as requested, our team will call you shortly about {facts}. Reply here if another time suits you better"
    else:
        body = f"a reminder that the renewal for {facts} is coming up. Reply RENEW for a payment link"
    return f"Hi {first}, {body}. – Ampera BPO"


def gemini_message(f: Followup, c: Customer, p: Policy) -> str:
    s = get_settings()
    client = genai.Client(api_key=s.google_api_key, http_options=types.HttpOptions(timeout=20_000))
    resp = client.models.generate_content(
        model=s.gemini_flash_model,
        contents=PROMPT.format(first=c.full_name.split()[0], policy_no=p.policy_no, plan=p.plan_name,
                               premium=format_inr(p.premium_inr), due=format_date(p.renewal_date),
                               purpose=PURPOSE[f.kind], reason=f.reason,
                               link=f"; payment link {f.payment_link_url}" if f.payment_link_url else ""),
        config=types.GenerateContentConfig(temperature=0.4,
                                           thinking_config=types.ThinkingConfig(thinking_level="LOW")))
    text = (resp.text or "").strip()
    if not text:
        raise ValueError("empty Gemini response")
    return text[:600]


def whatsapp_template(f: Followup, c: Customer, p: Policy, s) -> tuple[str, list[str]]:
    """(approved template name, body params) for this follow-up's kind; name "" = record only, don't send.
    Param order must match the {{n}} placeholders of the templates registered in Meta."""
    first = c.full_name.split()[0]
    if s.whatsapp_template_name == "hello_world":  # Meta's no-param connectivity test: send every kind with it
        return s.whatsapp_template_name, []
    if f.kind == FollowupKind.payment_link:
        return s.whatsapp_template_name, [first, format_inr(p.premium_inr), f.payment_link_url or ""]
    if f.kind == FollowupKind.callback:
        return s.whatsapp_callback_template, [first, p.policy_no]
    return s.whatsapp_reminder_template, [first, p.policy_no, format_inr(p.premium_inr), format_date(p.renewal_date)]


def send_due_followups(db: Session, now: datetime | None = None) -> list[int]:
    """Marks due scheduled follow-ups as sent (sandbox: the message is stored, never delivered).
    Gemini Flash writes the text when a key is set, else (or on any failure) the deterministic template."""
    now = now or utcnow()
    s = get_settings()
    use_ai = bool(s.google_api_key)
    due = db.scalars(select(Followup).where(
        Followup.status == FollowupStatus.scheduled,
        or_(Followup.due_at <= now,
            and_(Followup.kind == FollowupKind.callback, Followup.due_at <= now + CALLBACK_HEADS_UP)))
                     .order_by(Followup.due_at).limit(BATCH)).all()
    sent = []
    for f in due:
        p, c = db.get(Policy, f.policy_id), db.get(Customer, f.customer_id)
        if f.kind == FollowupKind.payment_link and not f.payment_link_url:
            try:
                f.payment_link_url = payment_link_for(p, c, f"{p.policy_no}-f{f.id}")
            except Exception as e:  # Razorpay/network error: leave it due, the next run retries
                log.warning("Payment link creation failed for followup %d: %s", f.id, e)
                continue
        text = None
        # Only fresh follow-ups use Gemini: the seeded backlog would burn the Flash quota post-call analysis needs.
        if use_ai and now - f.due_at <= AI_FRESHNESS:
            try:
                text = gemini_message(f, c, p)
            except Exception as e:  # quota/outage: template for the rest of this run, the timer keeps working
                log.warning("Gemini follow-up message failed, using template: %s", e)
                use_ai = False
        f.message_text = text or template_message(f, c, p)
        template, params = whatsapp_template(f, c, p, s) if s.whatsapp_enabled else ("", [])
        if template \
                and (not s.contact_allowlist_prefix or c.customer_code.startswith(s.contact_allowlist_prefix)):
            try:
                send_template(c.phone, params, c.preferred_language, name=template)
            except Exception as e:  # Meta/network error: leave it due, the next run retries
                log.warning("WhatsApp send failed for followup %d: %s", f.id, e)
                db.commit()  # keep the stored message_text/payment_link_url, just don't mark it sent
                continue
        f.status, f.sent_at = FollowupStatus.sent, now
        db.commit()  # per item: a later failure doesn't undo messages already "sent"
        sent.append(f.id)
    return sent


def dial_due_callbacks(db: Session, now: datetime | None = None) -> list[int]:
    """Rings customers whose callback time has come (heads-up message already sent) and marks those `done`.
    Needs Plivo; only allowlisted customers; callbacks missed by over CALLBACK_DIAL_WINDOW stay `sent` for a human."""
    now = now or utcnow()
    s = get_settings()
    if not s.plivo_enabled:
        return []
    due = db.scalars(select(Followup).where(
        Followup.kind == FollowupKind.callback, Followup.status == FollowupStatus.sent,
        Followup.due_at <= now, Followup.due_at >= now - CALLBACK_DIAL_WINDOW).order_by(Followup.due_at)).all()
    script = active_script(db, CallMode.renewal)
    called = []
    for f in due:
        c, p = db.get(Customer, f.customer_id), db.get(Policy, f.policy_id)
        if s.contact_allowlist_prefix and not c.customer_code.startswith(s.contact_allowlist_prefix):
            continue
        call = place_call(db, c, p, script_id=script.id if script else None)
        # One attempt only (no ringing every minute): a failed or unanswered ring shows in Call History.
        f.status = FollowupStatus.done
        db.commit()
        if call.status == CallStatus.failed:
            log.warning("Callback %d: Plivo could not ring the customer (call %s)", f.id, call.call_ref)
        called.append(f.id)
    return called


def start_scheduler(session_factory: sessionmaker) -> BackgroundScheduler:
    def job() -> None:
        with session_factory() as db:
            sent = send_due_followups(db)
            if sent:
                log.info("Follow-up timer sent %d sandbox message(s)", len(sent))
            if called := dial_due_callbacks(db):
                log.info("Follow-up timer rang %d callback(s)", len(called))

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(job, "interval", minutes=INTERVAL_MINUTES, id="followups", max_instances=1, coalesce=True)
    scheduler.start()
    return scheduler
