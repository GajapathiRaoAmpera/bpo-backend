from datetime import timedelta

from sqlalchemy import select

from app.core.db import utcnow
from app.models import Customer, Followup, Policy
from app.models.enums import FollowupKind, FollowupStatus
from app.services import followup_scheduler as fs


def _followup(db, kind, due) -> Followup:
    p = db.scalars(select(Policy)).first()
    f = Followup(policy_id=p.id, customer_id=p.customer_id, kind=kind, due_at=due, reason="Customer asked",
                 status=FollowupStatus.scheduled)
    db.add(f)
    db.commit()
    return f


def _drain(db, now, until_id):
    while sent := fs.send_due_followups(db, now):  # seeded backlog is processed in batches of 25
        if until_id in sent:
            return


def test_due_followups_are_sent_with_template(db):
    now = utcnow()
    due = _followup(db, FollowupKind.payment_link, now - timedelta(minutes=1))
    later = _followup(db, FollowupKind.callback, now + timedelta(hours=1))
    _drain(db, now, due.id)  # GOOGLE_API_KEY is blank in tests -> template
    db.refresh(due)
    db.refresh(later)
    assert due.status == FollowupStatus.sent and due.sent_at == now
    assert due.payment_link_url.startswith("https://pay.sandbox.assurevoice.in/")
    assert due.message_text == fs.template_message(due, db.get(Customer, due.customer_id), db.get(Policy, due.policy_id))
    assert due.payment_link_url in due.message_text and "₹" in due.message_text
    assert later.status == FollowupStatus.scheduled and later.message_text is None


def test_gemini_used_for_fresh_followups_and_falls_back(db, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(fs, "get_settings", lambda: type("S", (), {"google_api_key": "k"})())
    calls = []

    def fake(f, c, p):
        calls.append(f.id)
        return "Hi from Gemini – AssureVoice Insurance"

    monkeypatch.setattr(fs, "gemini_message", fake)
    old = _followup(db, FollowupKind.reminder, now - timedelta(days=2))  # backlog: template, no Gemini call
    fresh = _followup(db, FollowupKind.reminder, now - timedelta(minutes=1))
    _drain(db, now, fresh.id)
    db.refresh(old)
    db.refresh(fresh)
    assert fresh.message_text == "Hi from Gemini – AssureVoice Insurance" and old.id not in calls
    assert old.message_text.startswith("Hi ") and old.status == FollowupStatus.sent

    def boom(*_):
        raise RuntimeError("429 quota")

    monkeypatch.setattr(fs, "gemini_message", boom)
    failed = _followup(db, FollowupKind.callback, now)
    assert fs.send_due_followups(db, now) == [failed.id]
    db.refresh(failed)
    assert failed.status == FollowupStatus.sent and failed.message_text.endswith("– AssureVoice Insurance")
