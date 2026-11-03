"""Meta WhatsApp webhook: delivery/read receipts and inbound replies.

Meta has no user session, so these routes skip require(). The GET handshake only runs once, when you
register the webhook URL in the Meta dashboard. The POST route is rejected unless it carries a valid
X-Hub-Signature-256 for WHATSAPP_APP_SECRET. An inbound "RENEW" gets a payment link back as free-form text
(free inside Meta's 24 h service window); every other event is only logged."""

import hashlib
import hmac
import json
import logging
import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal, utcnow
from app.core.formatting import format_inr
from app.models import Customer, Followup, Policy
from app.models.enums import FollowupKind, FollowupStatus, PolicyStatus
from app.services.payments import payment_link_for
from app.services.whatsapp import send_text

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
log = logging.getLogger(__name__)


@router.get("/webhook")
def verify_webhook(request: Request) -> Response:
    """One-time handshake Meta runs when you save the webhook URL in the dashboard."""
    s = get_settings()
    q = request.query_params
    if (q.get("hub.mode") == "subscribe" and s.whatsapp_webhook_verify_token
            and q.get("hub.verify_token") == s.whatsapp_webhook_verify_token):
        return Response(content=q.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403, "Invalid verify token")


def renew_requests(event: dict) -> list[str]:
    """Phone numbers (as Meta sends them, e.g. 9198...) of inbound text messages that say RENEW."""
    return [m["from"] for e in event.get("entry", []) for ch in e.get("changes", [])
            for m in ch.get("value", {}).get("messages", [])
            if m.get("type") == "text" and m.get("text", {}).get("body", "").strip().upper() == "RENEW"]


def reply_with_link(wa_from: str) -> None:
    """Creates a payment link for the customer's next due policy and replies with it."""
    s = get_settings()
    last10 = re.sub(r"\D", "", wa_from)[-10:]
    with SessionLocal() as db:
        c = db.scalar(select(Customer).where(Customer.phone.like(f"%{last10}")))
        if c is None or (s.contact_allowlist_prefix and not c.customer_code.startswith(s.contact_allowlist_prefix)):
            log.info("RENEW from an unknown or non-allowlisted number, ignored")
            return
        p = db.scalar(select(Policy).where(Policy.customer_id == c.id, Policy.status == PolicyStatus.due)
                      .order_by(Policy.renewal_date))
        if p is None:
            send_text(c.phone, f"Hi {c.full_name.split()[0]}, you have no renewal due right now. – Ampera BPO")
            return
        f = Followup(policy_id=p.id, customer_id=c.id, kind=FollowupKind.payment_link, due_at=utcnow(),
                     reason="Customer replied RENEW on WhatsApp", status=FollowupStatus.scheduled)
        db.add(f)
        db.flush()
        try:
            f.payment_link_url = payment_link_for(p, c, f"{p.policy_no}-w{f.id}")
            f.message_text = (f"Hi {c.full_name.split()[0]}, here is your secure link to renew {p.policy_no} "
                              f"({format_inr(p.premium_inr)}): {f.payment_link_url} – Ampera BPO")
            send_text(c.phone, f.message_text)
            f.status, f.sent_at = FollowupStatus.sent, utcnow()
        except Exception as e:  # left scheduled: the follow-up timer retries it with the template
            log.warning("RENEW reply failed for followup %d: %s", f.id, e)
        db.commit()


@router.post("/webhook")
async def receive_webhook(request: Request, background: BackgroundTasks) -> dict:
    s = get_settings()
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(s.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    if not (s.whatsapp_app_secret and hmac.compare_digest(sig, expected)):
        raise HTTPException(403, "Invalid signature")
    log.info("WhatsApp webhook event: %s", body.decode(errors="replace")[:2000])
    try:
        senders = renew_requests(json.loads(body))
    except (ValueError, AttributeError, KeyError, TypeError):
        senders = []
    for wa_from in senders:
        background.add_task(reply_with_link, wa_from)  # after the 200, so Meta doesn't time out
    return {"status": "ok"}  # Meta expects a fast 200; nothing slow happens on this path
