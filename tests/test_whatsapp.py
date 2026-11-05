"""Real WhatsApp sends via followup_scheduler (Meta Cloud API mocked), and the integrations status flip."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.api.v1.integrations import list_integrations
from app.core.config import get_settings
from app.core.db import utcnow
from app.models import Call, Customer, Followup, Policy
from app.models.enums import CallMode, CallStatus, FollowupKind, FollowupStatus
from app.services import followup_scheduler as fs
from app.services import voice_tools as vt
from app.services import whatsapp


@pytest.fixture
def whatsapp_on(monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1060212733833186")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("WHATSAPP_TEMPLATE_NAME", "payment_reminder")
    monkeypatch.setenv("CONTACT_ALLOWLIST_PREFIX", "TST-")
    for name in ("WHATSAPP_CALLBACK_TEMPLATE", "WHATSAPP_REMINDER_TEMPLATE", "WHATSAPP_CALLBACK_CONFIRM_TEMPLATE"):
        monkeypatch.setenv(name, "")  # the developer's .env may set these
    get_settings.cache_clear()
    resp = MagicMock(status_code=200)
    post = MagicMock(return_value=resp)
    monkeypatch.setattr(whatsapp.httpx, "post", post)
    yield post
    get_settings.cache_clear()


@pytest.fixture
def followup(db):
    """A due follow-up for a customer with no other scheduled follow-ups, so it's processed alone and
    the customer_code is safe to repoint at a test prefix without disturbing other seeded follow-ups."""
    busy = select(Followup.customer_id).where(Followup.status == FollowupStatus.scheduled)
    p = db.scalars(select(Policy).join(Customer, Customer.id == Policy.customer_id)
                   .where(Customer.id.not_in(busy)).order_by(Policy.id)).first()
    c = db.get(Customer, p.customer_id)
    old_code = c.customer_code
    # due_at far in the past so this is always first in line, ahead of any seeded backlog (BATCH=25 limit)
    f = Followup(policy_id=p.id, customer_id=c.id, kind=FollowupKind.payment_link,
                 due_at=utcnow() - timedelta(days=3650), reason="test", status=FollowupStatus.scheduled)
    db.add(f)
    db.commit()
    yield f, c
    c.customer_code = old_code
    db.commit()


def test_allowlisted_customer_gets_a_real_whatsapp_send(db, whatsapp_on, followup):
    f, c = followup
    c.customer_code = "TST-0001"
    db.commit()
    fs.send_due_followups(db, utcnow())
    db.refresh(f)
    assert whatsapp_on.call_args.args[0].endswith(f"/{get_settings().whatsapp_phone_number_id}/messages")
    assert f.status == FollowupStatus.sent


def test_callback_is_recorded_but_not_sent_without_its_own_template(db, whatsapp_on, followup):
    f, c = followup
    c.customer_code = "TST-0009"
    f.kind = FollowupKind.callback
    db.commit()
    fs.send_due_followups(db, utcnow())
    db.refresh(f)
    assert whatsapp_on.call_count == 0 and f.status == FollowupStatus.sent


@pytest.mark.parametrize("kind,env,n_params", [(FollowupKind.callback, "WHATSAPP_CALLBACK_TEMPLATE", 2),
                                                (FollowupKind.reminder, "WHATSAPP_REMINDER_TEMPLATE", 4)])
def test_callback_and_reminder_send_with_their_own_template(db, whatsapp_on, followup, monkeypatch,
                                                            kind, env, n_params):
    monkeypatch.setenv(env, "my_template")
    get_settings.cache_clear()
    f, c = followup
    c.customer_code = "TST-0010"
    f.kind = kind
    db.commit()
    fs.send_due_followups(db, utcnow())
    db.refresh(f)
    tpl = whatsapp_on.call_args.kwargs["json"]["template"]
    assert tpl["name"] == "my_template" and f.status == FollowupStatus.sent
    assert len(tpl["components"][0]["parameters"]) == n_params


def test_non_allowlisted_customer_is_not_messaged(db, whatsapp_on, followup):
    f, c = followup
    c.customer_code = "CUS-0001"
    db.commit()
    fs.send_due_followups(db, utcnow())
    db.refresh(f)
    assert whatsapp_on.call_count == 0
    assert f.status == FollowupStatus.sent  # still recorded for the UI, just not really sent


def test_send_failure_leaves_the_followup_due_for_retry(db, whatsapp_on, followup):
    f, c = followup
    c.customer_code = "TST-0002"
    db.commit()
    whatsapp_on.return_value = MagicMock(status_code=401, raise_for_status=MagicMock(
        side_effect=Exception("401 invalid token")))
    assert f.id not in fs.send_due_followups(db, utcnow())
    db.refresh(f)
    assert f.status == FollowupStatus.scheduled


def test_whatsapp_integration_row_is_connected_once_configured(db, whatsapp_on):
    row = next(r for r in list_integrations(db) if r.key == "whatsapp")
    assert row.status == "connected"


@pytest.fixture
def verified_call(db):
    """A verified, in-progress call for a customer with no scheduled follow-ups (same isolation reason
    as the `followup` fixture)."""
    busy = select(Followup.customer_id).where(Followup.status == FollowupStatus.scheduled)
    p = db.scalars(select(Policy).join(Customer, Customer.id == Policy.customer_id)
                   .where(Customer.id.not_in(busy)).order_by(Policy.id)).first()
    c = db.get(Customer, p.customer_id)
    old_code = c.customer_code
    call = Call(call_ref=f"CALL-WA-{utcnow().timestamp()}", direction="inbound", mode=CallMode.inbound,
                status=CallStatus.in_progress, started_at=utcnow(), customer_id=c.id, policy_id=p.id)
    db.add(call)
    db.commit()
    state = vt.CallState(call_id=call.id, started_at=call.started_at)
    assert vt.verify_customer(db, state, p.policy_no, c.dob.isoformat())["ok"]
    yield state, c, p
    c.customer_code = old_code
    db.commit()


def test_send_payment_link_sends_real_whatsapp_when_allowlisted(db, whatsapp_on, verified_call):
    state, c, p = verified_call
    c.customer_code = "TST-0001"
    db.commit()
    r = vt.send_payment_link(db, state, p.policy_no)
    assert r["ok"] is True and whatsapp_on.call_count == 1
    f = db.scalars(select(Followup).where(Followup.call_id == state.call_id)).first()
    assert f.status == FollowupStatus.sent


def test_send_payment_link_stays_simulated_when_not_allowlisted(db, whatsapp_on, verified_call):
    state, c, p = verified_call
    c.customer_code = "CUS-0001"
    db.commit()
    r = vt.send_payment_link(db, state, p.policy_no)
    assert r["ok"] is True and whatsapp_on.call_count == 0


def test_send_payment_link_reports_a_real_whatsapp_failure(db, whatsapp_on, verified_call):
    state, c, p = verified_call
    c.customer_code = "TST-0002"
    db.commit()
    whatsapp_on.return_value = MagicMock(status_code=401, raise_for_status=MagicMock(
        side_effect=Exception("401 invalid token")))
    r = vt.send_payment_link(db, state, p.policy_no)
    assert r["ok"] is False
    f = db.scalars(select(Followup).where(Followup.call_id == state.call_id)).first()
    assert f.status == FollowupStatus.scheduled  # the background timer will retry it


# --- Callbacks: confirmation on the call, WhatsApp 5 minutes before, Plivo ring at the promised time ---

@pytest.fixture
def plivo_rest(monkeypatch):
    monkeypatch.setenv("PLIVO_AUTH_ID", "MAXXXXXXXXXXXXXXXXXX")
    monkeypatch.setenv("PLIVO_AUTH_TOKEN", "plivo-test-token")
    monkeypatch.setenv("PLIVO_FROM_NUMBER", "918031234567")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://av.example.test")
    get_settings.cache_clear()
    rest = MagicMock()
    monkeypatch.setattr("app.services.campaigns.plivo.RestClient", rest)
    yield rest
    get_settings.cache_clear()


def _drain(db, now):
    while fs.send_due_followups(db, now):  # seeded backlog goes out in batches of 25
        pass


@pytest.mark.parametrize("minutes_ahead,heads_up_now", [(3, True), (10, False)])
def test_callback_heads_up_goes_out_5_minutes_early(db, whatsapp_on, followup, minutes_ahead, heads_up_now):
    f, c = followup
    c.customer_code = "TST-0011"
    f.kind, f.due_at = FollowupKind.callback, utcnow() + timedelta(minutes=minutes_ahead)
    db.commit()
    _drain(db, utcnow())
    db.refresh(f)
    assert (f.status == FollowupStatus.sent) is heads_up_now


def test_due_callback_rings_once_and_is_done(db, whatsapp_on, followup, plivo_rest):
    f, c = followup
    c.customer_code = "TST-0012"
    f.kind, f.status, f.due_at = FollowupKind.callback, FollowupStatus.sent, utcnow() - timedelta(minutes=1)
    db.commit()
    assert fs.dial_due_callbacks(db, utcnow()) == [f.id]
    db.refresh(f)
    assert f.status == FollowupStatus.done
    assert plivo_rest.return_value.calls.create.call_args.kwargs["to_"] == "91" + c.phone[-10:]
    assert fs.dial_due_callbacks(db, utcnow()) == []  # never rung twice


def test_long_missed_callback_is_not_rung_late(db, whatsapp_on, followup, plivo_rest):
    f, c = followup
    c.customer_code = "TST-0013"
    f.kind, f.status, f.due_at = FollowupKind.callback, FollowupStatus.sent, utcnow() - timedelta(hours=2)
    db.commit()
    assert f.id not in fs.dial_due_callbacks(db, utcnow())
    assert plivo_rest.return_value.calls.create.call_count == 0


def test_booking_a_callback_on_the_call_sends_a_confirmation(db, whatsapp_on, verified_call, monkeypatch):
    monkeypatch.setenv("WHATSAPP_CALLBACK_CONFIRM_TEMPLATE", "renewal_callback_confirmed")
    get_settings.cache_clear()
    state, c, p = verified_call
    c.customer_code = "TST-0014"
    db.commit()
    r = vt.schedule_followup(db, state, p.policy_no, "tomorrow 18:00", "wants to talk to family")
    tpl = whatsapp_on.call_args.kwargs["json"]["template"]
    assert r["ok"] is True and tpl["name"] == "renewal_callback_confirmed"
    assert [x["text"] for x in tpl["components"][0]["parameters"]][2] == r["scheduled_for_spoken"]
