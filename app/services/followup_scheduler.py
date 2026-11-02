"""Follow-up timer: every 2 minutes, scheduled follow-ups that are due are marked `sent` with a WhatsApp-style
message. The message is always stored on the follow-up; it is also really sent over WhatsApp when
`whatsapp_enabled` and the customer passes CONTACT_ALLOWLIST_PREFIX (see app.core.config), otherwise it stays
sandbox-only (stored, never delivered)."""

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from google import genai
from google.genai import types
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.db import utcnow
from app.core.formatting import format_date, format_inr
from app.models import Customer, Followup, Policy
from app.models.enums import FollowupKind, FollowupStatus
from app.services.whatsapp import send_template

log = logging.getLogger(__name__)

INTERVAL_MINUTES = 2
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


def send_due_followups(db: Session, now: datetime | None = None) -> list[int]:
    """Marks due scheduled follow-ups as sent (sandbox: the message is stored, never delivered).
    Gemini Flash writes the text when a key is set, else (or on any failure) the deterministic template."""
    now = now or utcnow()
    s = get_settings()
    use_ai = bool(s.google_api_key)
    due = db.scalars(select(Followup).where(Followup.status == FollowupStatus.scheduled, Followup.due_at <= now)
                     .order_by(Followup.due_at).limit(BATCH)).all()
    sent = []
    for f in due:
        p, c = db.get(Policy, f.policy_id), db.get(Customer, f.customer_id)
        if f.kind == FollowupKind.payment_link and not f.payment_link_url:
            f.payment_link_url = f"{s.payment_link_base}/{p.policy_no.lower()}-f{f.id}"
        text = None
        # Only fresh follow-ups use Gemini: the seeded backlog would burn the Flash quota post-call analysis needs.
        if use_ai and now - f.due_at <= AI_FRESHNESS:
            try:
                text = gemini_message(f, c, p)
            except Exception as e:  # quota/outage: template for the rest of this run, the timer keeps working
                log.warning("Gemini follow-up message failed, using template: %s", e)
                use_ai = False
        f.message_text = text or template_message(f, c, p)
        if s.whatsapp_enabled and (not s.contact_allowlist_prefix
                                    or c.customer_code.startswith(s.contact_allowlist_prefix)):
            try:
                # shortcut: Meta's "hello_world" connectivity-test template takes no parameters; any real
                # approved template takes the 3 below. Upgrade when more than one real template is needed.
                params = [] if s.whatsapp_template_name == "hello_world" else [
                    c.full_name.split()[0], format_inr(p.premium_inr), f.payment_link_url or ""]
                send_template(c.phone, params)
            except Exception as e:  # Meta/network error: leave it due, the next run retries
                log.warning("WhatsApp send failed for followup %d: %s", f.id, e)
                db.commit()  # keep the stored message_text/payment_link_url, just don't mark it sent
                continue
        f.status, f.sent_at = FollowupStatus.sent, now
        db.commit()  # per item: a later failure doesn't undo messages already "sent"
        sent.append(f.id)
    return sent


def start_scheduler(session_factory: sessionmaker) -> BackgroundScheduler:
    def job() -> None:
        with session_factory() as db:
            sent = send_due_followups(db)
            if sent:
                log.info("Follow-up timer sent %d sandbox message(s)", len(sent))

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(job, "interval", minutes=INTERVAL_MINUTES, id="followups", max_instances=1, coalesce=True)
    scheduler.start()
    return scheduler
