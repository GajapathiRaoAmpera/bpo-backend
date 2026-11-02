"""Meta WhatsApp webhook: delivery/read receipts and inbound replies.

Meta has no user session, so these routes skip require(). The GET handshake only runs once, when you
register the webhook URL in the Meta dashboard. The POST route is rejected unless it carries a valid
X-Hub-Signature-256 for WHATSAPP_APP_SECRET. Events are only logged for now; nothing acts on an inbound
reply yet (shortcut: no model/UI to show one, add when a feature needs it)."""

import hashlib
import hmac
import logging

from fastapi import APIRouter, HTTPException, Request, Response

from app.core.config import get_settings

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


@router.post("/webhook")
async def receive_webhook(request: Request) -> dict:
    s = get_settings()
    body = await request.body()
    sig = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(s.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    if not (s.whatsapp_app_secret and hmac.compare_digest(sig, expected)):
        raise HTTPException(403, "Invalid signature")
    log.info("WhatsApp webhook event: %s", body.decode(errors="replace")[:2000])
    return {"status": "ok"}  # Meta expects a fast 200; nothing slow happens on this path
