"""Plivo telephony: real dialling on launch (Plivo mocked), signed webhooks, one-time stream token."""

from unittest.mock import MagicMock

import pytest
from plivo.utils.signature_v3 import construct_post_url, get_signature_v3
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.api.v1 import telephony
from app.core.config import get_settings
from app.models import Call, Campaign, CampaignTarget, Customer, Policy
from app.models.enums import CallMode, CallStatus, CampaignStatus, Role, TargetStatus
from app.services.scripts import active_script
from app.voice.session import SESSIONS

API = "/api/v1"
BASE = "https://av.example.test"
TOKEN = "plivo-test-token"


@pytest.fixture
def plivo_on(monkeypatch):
    monkeypatch.setenv("PLIVO_AUTH_ID", "MAXXXXXXXXXXXXXXXXXX")
    monkeypatch.setenv("PLIVO_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("PLIVO_FROM_NUMBER", "918031234567")
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.setenv("CONTACT_ALLOWLIST_PREFIX", "TST-")
    get_settings.cache_clear()
    rest = MagicMock()
    monkeypatch.setattr("app.services.campaigns.plivo.RestClient", rest)
    yield rest
    get_settings.cache_clear()


def signed(path: str, params: dict, token: str = TOKEN) -> dict:
    nonce = "12345"
    sig = get_signature_v3(token.encode(), construct_post_url(BASE + path, params), nonce.encode()).decode()
    return {"X-Plivo-Signature-V3": sig, "X-Plivo-Signature-V3-Nonce": nonce}


@pytest.fixture
def campaign(db):
    """A running campaign with two targets, only one of them an allowlisted (TST-) colleague."""
    first = db.scalars(select(Policy).order_by(Policy.id)).first()
    other = db.scalars(select(Policy).where(Policy.customer_id != first.customer_id).order_by(Policy.id)).first()
    policies = [first, other]
    colleague = db.get(Customer, policies[0].customer_id)
    old_code = colleague.customer_code
    colleague.customer_code = "TST-0001"
    script = active_script(db, CallMode.renewal)
    c = Campaign(name="Plivo test", product=policies[0].product, segment={"renewal_within_days": 30, "languages": []},
                 script_id=script.id, status=CampaignStatus.approved, created_by=script.created_by, total_targets=2)
    db.add(c)
    db.flush()
    db.add_all(CampaignTarget(campaign_id=c.id, policy_id=p.id) for p in policies)
    db.commit()
    yield c, colleague, policies[1]
    colleague.customer_code = old_code
    db.commit()


def test_launch_dials_only_allowlisted_through_plivo(login, db, plivo_on, campaign):
    c, colleague, stranger_policy = campaign
    r = login(Role.campaign_manager).post(f"{API}/campaigns/{c.id}/launch")
    assert r.status_code == 200, r.text
    (call_id,) = r.json()["call_ids"]
    kwargs = plivo_on.return_value.calls.create.call_args.kwargs
    assert kwargs["to_"] == "91" + colleague.phone[-10:] and kwargs["from_"] == "918031234567"
    assert kwargs["answer_url"] == f"{BASE}/api/v1/telephony/plivo/answer/{call_id}"
    db.expire_all()
    call = db.get(Call, call_id)
    assert not call.is_simulated and call.status == CallStatus.in_progress and call.ended_at is None
    stranger = db.scalar(select(CampaignTarget).where(CampaignTarget.campaign_id == c.id,
                                                      CampaignTarget.policy_id == stranger_policy.id))
    assert stranger.status == TargetStatus.pending and stranger.call_id is None


def test_plivo_error_marks_call_failed(login, db, plivo_on, campaign):
    c, _, _ = campaign
    plivo_on.return_value.calls.create.side_effect = RuntimeError("Plivo down")
    (call_id,) = login(Role.campaign_manager).post(f"{API}/campaigns/{c.id}/launch").json()["call_ids"]
    db.expire_all()
    assert db.get(Call, call_id).status == CallStatus.failed
    assert db.scalar(select(CampaignTarget).where(CampaignTarget.call_id == call_id)).status == TargetStatus.failed


def test_webhooks_reject_bad_signature(client, plivo_on):
    for path in ("/api/v1/telephony/plivo/answer/1", "/api/v1/telephony/plivo/hangup/1"):
        assert client.post(path, data={"CallUUID": "x"}).status_code == 403
        bad = signed(path, {"CallUUID": "x"}, token="wrong-token")
        assert client.post(path, data={"CallUUID": "x"}, headers=bad).status_code == 403


def test_answer_streams_to_one_time_websocket_then_hangup_closes(client, login, db, plivo_on, campaign):
    c, _, _ = campaign
    (call_id,) = login(Role.campaign_manager).post(f"{API}/campaigns/{c.id}/launch").json()["call_ids"]
    path = f"/api/v1/telephony/plivo/answer/{call_id}"
    form = {"CallUUID": "abc", "CallStatus": "in-progress"}
    r = client.post(path, data=form, headers=signed(path, form))
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    token = telephony._STREAM_TOKENS[call_id]
    assert f"wss://av.example.test/api/v1/telephony/plivo/ws/{call_id}/{token}</Stream>" in r.text
    assert 'bidirectional="true"' in r.text and 'contentType="audio/x-mulaw;rate=8000"' in r.text
    db.expire_all()
    assert db.get(Campaign, c.id).connected == 1

    with pytest.raises(WebSocketDisconnect):  # wrong token is refused, and the real one is still unused
        with client.websocket_connect(f"/api/v1/telephony/plivo/ws/{call_id}/guess") as ws:
            ws.receive_text()

    path = f"/api/v1/telephony/plivo/hangup/{call_id}"
    form = {"CallUUID": "abc", "HangupCause": "NORMAL_HANGUP"}
    assert client.post(path, data=form, headers=signed(path, form)).status_code == 200
    db.expire_all()
    assert db.get(Call, call_id).ended_at is not None and call_id not in SESSIONS
    assert call_id not in telephony._STREAM_TOKENS


def test_unanswered_call_fails_target_for_retry(client, login, db, plivo_on, campaign):
    c, _, _ = campaign
    (call_id,) = login(Role.campaign_manager).post(f"{API}/campaigns/{c.id}/launch").json()["call_ids"]
    path = f"/api/v1/telephony/plivo/hangup/{call_id}"
    form = {"CallUUID": "abc", "HangupCause": "NO_ANSWER"}
    assert client.post(path, data=form, headers=signed(path, form)).status_code == 200
    db.expire_all()
    assert db.get(Call, call_id).ended_at is not None
    assert db.scalar(select(CampaignTarget).where(CampaignTarget.call_id == call_id)).status == TargetStatus.failed
