"""Razorpay webhook: marks a renewal as paid. Razorpay has no user session, so this route skips require();
it is rejected unless X-Razorpay-Signature is a valid HMAC of the body with RAZORPAY_WEBHOOK_SECRET."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.models import Followup, Policy
from app.models.enums import AuditAction, FollowupKind, FollowupStatus, PolicyStatus
from app.services.payments import valid_signature

router = APIRouter(prefix="/payments", tags=["payments"])
log = logging.getLogger(__name__)


@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    body = await request.body()
    if not valid_signature(body, request.headers.get("X-Razorpay-Signature", "")):
        raise HTTPException(403, "Invalid signature")
    event = json.loads(body)
    if event.get("event") != "payment_link.paid":
        return {"status": "ignored"}
    link = event["payload"]["payment_link"]["entity"]
    f = db.scalar(select(Followup).where(Followup.kind == FollowupKind.payment_link,
                                         Followup.payment_link_url == link.get("short_url")))
    if f is None:
        log.warning("Razorpay paid event for unknown link %s", link.get("id"))
        return {"status": "unknown_link"}  # 200 so Razorpay doesn't retry forever
    if f.status != FollowupStatus.done:  # Razorpay retries deliveries; only act once
        f.status = FollowupStatus.done
        db.get(Policy, f.policy_id).status = PolicyStatus.renewed
        db.commit()
        audit.record(db, request, AuditAction.update, entity_type="followup", entity_id=f.id,
                     detail={"action": "payment_received", "razorpay_link": link.get("id"),
                             "amount_paise": link.get("amount_paid")})
    return {"status": "ok"}
