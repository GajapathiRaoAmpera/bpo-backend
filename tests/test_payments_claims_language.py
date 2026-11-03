"""Razorpay links + paid webhook, WhatsApp RENEW reply, language choice, claim intake, and the bot's
interruption/idle settings. Razorpay and Meta are mocked; nothing leaves the machine."""

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.api.v1 import whatsapp as wa_api
from app.core.config import get_settings
from app.core.db import utcnow
from app.models import Call, Claim, Customer, Followup, Policy
from app.models.enums import (
    CallMode, CallStatus, ClaimStatus, FollowupKind, FollowupStatus, Language, PolicyStatus, Role,
)
from app.services import payments, voice_tools as vt, whatsapp
from app.voice import bot

SECRET = "rzp-webhook-secret"
LINK = "https://rzp.io/rzp/test123"


def _verified(db, offset: int) -> tuple[vt.CallState, Policy, Customer]:
    p = db.scalars(select(Policy).order_by(Policy.id).offset(offset)).first()
    c = db.get(Customer, p.customer_id)
    call = Call(call_ref=f"CALL-P-{time.time_ns()}", direction="inbound", mode=CallMode.inbound,
                status=CallStatus.in_progress, started_at=utcnow())
    db.add(call)
    db.commit()
    s = vt.CallState(call_id=call.id, started_at=call.started_at)
    assert vt.verify_customer(db, s, p.policy_no, c.dob.isoformat())["ok"]
    return s, p, c


@pytest.fixture
def razorpay_on(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_x")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    get_settings.cache_clear()
    n = iter(range(10**6))
    post = MagicMock(side_effect=lambda *a, **k: MagicMock(
        status_code=200, json=MagicMock(return_value={"short_url": f"{LINK}{next(n)}{time.time_ns()}"})))
    monkeypatch.setattr(payments.httpx, "post", post)
    yield post
    get_settings.cache_clear()


def _signed(event: dict) -> tuple[bytes, dict]:
    body = json.dumps(event).encode()
    return body, {"X-Razorpay-Signature": hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest(),
                  "Content-Type": "application/json"}


# --- payments ------------------------------------------------------------------

def test_payment_link_comes_from_razorpay_in_paise(db, razorpay_on):
    s, p, _ = _verified(db, 20)
    assert vt.send_payment_link(db, s, p.policy_no)["ok"]
    sent = razorpay_on.call_args.kwargs["json"]
    assert sent["amount"] == p.premium_inr * 100 and sent["notify"] == {"sms": False, "email": False}
    f = db.scalar(select(Followup).where(Followup.call_id == s.call_id))
    assert f.payment_link_url.startswith(LINK)


def test_razorpay_failure_is_reported_not_faked(db, razorpay_on):
    razorpay_on.side_effect = Exception("503")
    s, p, _ = _verified(db, 21)
    r = vt.send_payment_link(db, s, p.policy_no)
    assert not r["ok"] and "schedule_followup" in r["error"]
    assert db.scalar(select(Followup).where(Followup.call_id == s.call_id)) is None


def test_webhook_rejects_bad_signature(client, razorpay_on):
    r = client.post("/api/v1/payments/razorpay/webhook", content=b"{}", headers={"X-Razorpay-Signature": "bad"})
    assert r.status_code == 403


def test_paid_webhook_marks_followup_done_and_policy_renewed_once(client, db, razorpay_on):
    s, p, _ = _verified(db, 22)
    old_status = p.status
    vt.send_payment_link(db, s, p.policy_no)
    url = db.scalar(select(Followup.payment_link_url).where(Followup.call_id == s.call_id))
    body, headers = _signed({"event": "payment_link.paid",
                             "payload": {"payment_link": {"entity": {"id": "plink_1", "short_url": url,
                                                                     "amount_paid": p.premium_inr * 100}}}})
    for _ in range(2):  # Razorpay retries: the second delivery must be a no-op
        assert client.post("/api/v1/payments/razorpay/webhook", content=body, headers=headers).status_code == 200
    db.expire_all()
    f = db.scalar(select(Followup).where(Followup.call_id == s.call_id))
    assert f.status == FollowupStatus.done and db.get(Policy, p.id).status == PolicyStatus.renewed
    db.get(Policy, p.id).status = old_status
    db.commit()


# --- WhatsApp ------------------------------------------------------------------

def test_template_language_falls_back_to_english(monkeypatch):
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "renewal_payment_link")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_LANGS", "en,ta")
    get_settings.cache_clear()
    try:
        assert whatsapp.template_lang(Language.tamil) == "ta"
        assert whatsapp.template_lang(Language.kannada) == "en"  # not approved yet
    finally:
        get_settings.cache_clear()


def test_renew_reply_is_detected():
    event = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "919876543210", "type": "text", "text": {"body": " renew "}},
        {"from": "919876543211", "type": "text", "text": {"body": "hello"}}]}}]}]}
    assert wa_api.renew_requests(event) == ["919876543210"]


def test_renew_reply_sends_a_link_as_free_text(db, razorpay_on, monkeypatch):
    p = db.scalars(select(Policy).where(Policy.status == PolicyStatus.due).order_by(Policy.id)).first()
    c = db.get(Customer, p.customer_id)
    sent = MagicMock()
    monkeypatch.setattr(wa_api, "send_text", sent)
    wa_api.reply_with_link("91" + c.phone)
    db.expire_all()
    assert LINK in sent.call_args.args[1]
    f = db.scalars(select(Followup).where(Followup.customer_id == c.id).order_by(Followup.id.desc())).first()
    assert f.status == FollowupStatus.sent and f.payment_link_url.startswith(LINK)


# --- language ------------------------------------------------------------------

def test_language_saved_only_after_verification(db):
    p = db.scalars(select(Policy).order_by(Policy.id).offset(23)).first()
    c = db.get(Customer, p.customer_id)
    old = c.preferred_language
    call = Call(call_ref=f"CALL-L-{time.time_ns()}", direction="inbound", mode=CallMode.inbound,
                status=CallStatus.in_progress, started_at=utcnow())
    db.add(call)
    db.commit()
    s = vt.CallState(call_id=call.id, started_at=call.started_at)
    assert not vt.set_language(db, s, "klingon")["ok"]
    r = vt.set_language(db, s, "Telugu")
    assert r["ok"] and not r["saved_to_record"] and s.language == Language.telugu
    db.refresh(c)
    assert c.preferred_language == old
    assert vt.verify_customer(db, s, p.policy_no, c.dob.isoformat())["ok"]
    assert vt.set_language(db, s, "malayalam")["saved_to_record"]
    db.refresh(c)
    assert c.preferred_language == Language.malayalam
    c.preferred_language = old
    db.commit()


# --- claims --------------------------------------------------------------------

def test_claim_needs_verification_and_a_past_date(db):
    s, p, _ = _verified(db, 24)
    unverified = vt.CallState(call_id=s.call_id, started_at=s.started_at)
    assert not vt.register_claim(db, unverified, p.policy_no, "2026-10-01", "Car hit")["ok"]
    assert not vt.register_claim(db, s, p.policy_no, "2099-01-01", "Car hit")["ok"]
    r = vt.register_claim(db, s, p.policy_no, "2026-10-01", "Rear bumper hit at a signal in Chennai")
    assert r["ok"] and r["claim_no"].startswith("CLM-")
    assert db.scalar(select(Claim).where(Claim.claim_no == r["claim_no"])).status == ClaimStatus.registered


def test_claims_api_rbac_and_transitions(login, db):
    s, p, _ = _verified(db, 25)
    claim_no = vt.register_claim(db, s, p.policy_no, "2026-10-02", "Hospitalised")["claim_no"]
    cid = db.scalar(select(Claim.id).where(Claim.claim_no == claim_no))
    assert login(Role.human_agent).get("/api/v1/claims").status_code == 403
    c = login(Role.team_lead)
    assert any(r["claim_no"] == claim_no for r in c.get("/api/v1/claims").json())
    assert c.patch(f"/api/v1/claims/{cid}", json={"status": "settled"}).status_code == 409  # can't skip review
    assert c.patch(f"/api/v1/claims/{cid}", json={"status": "under_review"}).json()["status"] == "under_review"


# --- voice bot -----------------------------------------------------------------

def test_tools_survive_interruptions_and_prompt_has_the_call_rules():
    assert all(n in {t[0] for t in bot.TOOL_SPECS} for n in ("set_language", "register_claim"))
    for name, *_ in bot.TOOL_SPECS:
        assert hasattr(vt, name)
    script = MagicMock(system_prompt="SCRIPT")
    prompt = bot.build_prompt(script, MagicMock(mode=CallMode.inbound), None, None)
    for rule in ("set_language", "Kannada", "interrupts", "Never stay silent", "one moment"):
        assert rule in prompt
