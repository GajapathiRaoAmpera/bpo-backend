import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.db import utcnow
from app.core.formatting import IST, format_inr
from app.models import AuditLog, Call, Customer, Escalation, Followup, Policy
from app.models.enums import AuditAction, CallMode, CallOutcome, CallStatus, FollowupKind, Role
from app.services import voice_tools as vt
from app.services.post_call import decide_outcome
from app.voice.session import SESSIONS


def _state(db, mode=CallMode.inbound, policy: Policy | None = None) -> vt.CallState:
    call = Call(call_ref=f"CALL-T-{time.time_ns()}", direction="inbound", mode=mode, status=CallStatus.in_progress,
                started_at=utcnow(), customer_id=policy.customer_id if policy else None,
                policy_id=policy.id if policy else None)
    db.add(call)
    db.commit()
    return vt.CallState(call_id=call.id, started_at=call.started_at)


def _policy_and_customer(db, offset=0):
    p = db.scalars(select(Policy).order_by(Policy.id).offset(offset)).first()
    return p, db.get(Customer, p.customer_id)


# --- helpers -------------------------------------------------------------------

def test_normalize_policy_no():
    assert vt.normalize_policy_no("mot 2026 1234") == "MOT-2026-001234"
    assert vt.normalize_policy_no("HLT-2026-001334") == "HLT-2026-001334"


@pytest.mark.parametrize("raw", ["1985-01-15", "15-01-1985", "15/01/1985", "15 January 1985", "15th Jan 1985"])
def test_parse_dob(raw):
    assert vt.parse_dob(raw).isoformat() == "1985-01-15"


def test_parse_when_keeps_calls_inside_trai_window():
    now = datetime(2026, 10, 29, 10, 0, tzinfo=IST)
    assert vt.parse_when("2026-10-30T18:00:00+05:30", now).hour == 18
    late = vt.parse_when("2026-10-30T21:30:00", now)
    assert (late.date().isoformat(), late.hour) == ("2026-10-31", 9)
    assert vt.parse_when("tomorrow evening", now).date().isoformat() == "2026-10-30"


# --- guardrails ------------------------------------------------------------------

def test_no_policy_details_before_verification(db):
    p, _ = _policy_and_customer(db)
    s = _state(db)
    r = vt.get_policy_details(db, s, p.policy_no)
    assert not r["ok"] and "not verified" in r["error"]
    assert str(p.premium_inr) not in str(r)
    assert not vt.send_payment_link(db, s, p.policy_no)["ok"]


def test_verify_then_details_payment_link_and_callback(db):
    p, c = _policy_and_customer(db)
    s = _state(db)
    assert not vt.verify_customer(db, s, p.policy_no, "1900-01-01")["ok"]
    r = vt.verify_customer(db, s, p.policy_no.replace("-", " "), c.dob.isoformat())
    assert r["ok"] and s.verified and db.get(Call, s.call_id).verified

    d = vt.get_policy_details(db, s, p.policy_no)
    assert d["premium_inr"] == p.premium_inr and d["premium_spoken"] == format_inr(p.premium_inr)

    assert vt.send_payment_link(db, s, p.policy_no)["ok"]
    assert vt.schedule_followup(db, s, p.policy_no, (utcnow() + timedelta(days=1)).isoformat(), "After salary")["ok"]
    kinds = {f.kind for f in db.scalars(select(Followup).where(Followup.call_id == s.call_id))}
    assert kinds == {FollowupKind.payment_link, FollowupKind.callback}
    assert s.actions.count("verify_customer") == 1


def test_verification_locks_after_three_failures(db):
    p, c = _policy_and_customer(db)
    s = _state(db)
    for _ in range(3):
        assert not vt.verify_customer(db, s, p.policy_no, "1900-01-01")["ok"]
    assert not vt.verify_customer(db, s, p.policy_no, c.dob.isoformat())["ok"]  # locked, even with the right DOB


def test_renewal_call_cannot_verify_as_someone_else(db):
    p1, _ = _policy_and_customer(db, 0)
    other = db.scalars(select(Policy).where(Policy.customer_id != p1.customer_id)).first()
    s = _state(db, CallMode.renewal, p1)
    assert not vt.verify_customer(db, s, other.policy_no, db.get(Customer, other.customer_id).dob.isoformat())["ok"]


def test_cannot_read_another_customers_policy_after_verifying(db):
    p1, c1 = _policy_and_customer(db, 0)
    other = db.scalars(select(Policy).where(Policy.customer_id != p1.customer_id)).first()
    s = _state(db)
    vt.verify_customer(db, s, p1.policy_no, c1.dob.isoformat())
    assert not vt.get_policy_details(db, s, other.policy_no)["ok"]


def test_escalation_creates_queue_item(db):
    p, c = _policy_and_customer(db)
    s = _state(db)
    vt.verify_customer(db, s, p.policy_no, c.dob.isoformat())
    assert vt.escalate_to_human(db, s, p.policy_no, "Disputes late fee")["ok"]
    esc = db.scalar(select(Escalation).where(Escalation.call_id == s.call_id))
    assert esc.reason == "Disputes late fee" and esc.status == "open"


def test_outcome_prefers_facts_over_model():
    assert decide_outcome({"send_payment_link"}, None, "not_interested", True) == CallOutcome.payment_link_sent
    assert decide_outcome({"escalate_to_human", "send_payment_link"}, None, None, True) == CallOutcome.escalated
    assert decide_outcome(set(), None, "payment_link_sent", True) == CallOutcome.info_provided  # no evidence
    assert decide_outcome(set(), None, None, False) == CallOutcome.no_answer
    assert decide_outcome({"send_payment_link"}, CallOutcome.callback_scheduled, None, True) == CallOutcome.callback_scheduled


# --- API ---------------------------------------------------------------------------

@pytest.mark.parametrize("role,code", [(Role.human_agent, 200), (Role.team_lead, 200), (Role.ops_manager, 403),
                                       (Role.qa_analyst, 403), (Role.super_admin, 403)])
def test_voice_api_rbac(login, role, code):
    assert login(role).get("/api/v1/voice/queue").status_code == code


def _wait_for_analysis(client, call_id):
    for _ in range(50):
        body = client.get(f"/api/v1/voice/sessions/{call_id}").json()
        if not body["analysis_pending"]:
            return body
        time.sleep(0.1)
    raise AssertionError("analysis did not finish")


def test_renewal_session_takeover_and_agent_outcome(login, db):
    c = login(Role.human_agent)
    item = c.get("/api/v1/voice/queue").json()[0]
    r = c.post("/api/v1/voice/sessions", json={"mode": "renewal", "policy_id": item["policy_id"]})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["customer"]["full_name"] == item["customer_name"] and not s["verified"]
    call_id = s["call_id"]

    # outcome is only for a human who took over
    assert c.post(f"/api/v1/voice/sessions/{call_id}/end", json={"outcome": "payment_link_sent"}).status_code == 422

    # the bot verifies the customer (tool service), then the agent takes over and wraps up
    policy = db.get(Policy, item["policy_id"])
    vt.verify_customer(db, SESSIONS[call_id].state, policy.policy_no, db.get(Customer, policy.customer_id).dob.isoformat())
    assert c.post(f"/api/v1/voice/sessions/{call_id}/takeover").json()["taken_over"]
    end = c.post(f"/api/v1/voice/sessions/{call_id}/end",
                 json={"outcome": "payment_link_sent", "note": "Customer will pay tonight."})
    assert end.status_code == 200 and end.json()["ended_at"]

    done = _wait_for_analysis(c, call_id)
    assert done["outcome"] == "payment_link_sent" and done["status"] == "completed"
    assert done["summary"] and done["qa"] is None  # no Gemini in tests: QA left for a human, never faked
    db.expire_all()
    link = db.scalar(select(Followup).where(Followup.call_id == call_id, Followup.kind == FollowupKind.payment_link))
    assert link.created_by is not None  # sent by the human agent
    actions = {a.action for a in db.scalars(select(AuditLog).where(AuditLog.entity_id == str(call_id),
                                                                   AuditLog.entity_type == "call"))}
    assert {AuditAction.call_start, AuditAction.takeover, AuditAction.call_end} <= actions


def test_unconnected_call_ends_as_no_answer_and_others_cannot_touch_it(login, client):
    c = login(Role.human_agent)
    call_id = c.post("/api/v1/voice/sessions", json={"mode": "inbound"}).json()["call_id"]
    c.post("/api/v1/auth/logout")
    other = login(Role.team_lead)
    assert other.post(f"/api/v1/voice/sessions/{call_id}/takeover").status_code == 404
    c = login(Role.human_agent)
    c.post(f"/api/v1/voice/sessions/{call_id}/end", json={})
    assert _wait_for_analysis(c, call_id)["outcome"] == "no_answer"
    assert c.post(f"/api/v1/voice/sessions/{call_id}/end", json={}).status_code == 409


def test_renewal_needs_policy(login):
    assert login(Role.human_agent).post("/api/v1/voice/sessions", json={"mode": "renewal"}).status_code == 422


def test_analysis_uses_gemini_but_system_facts_win(db, monkeypatch):
    import asyncio

    from app.services import post_call as pc

    p, c = _policy_and_customer(db)
    s = _state(db)
    vt.verify_customer(db, s, p.policy_no, c.dob.isoformat())
    vt.send_payment_link(db, s, p.policy_no)
    fake = pc.Analysis(summary="Customer agreed to renew.", outcome="not_interested", sentiment="positive",
                       next_action="Check payment tomorrow.",
                       qa=pc.QAChecks(ai_disclosure=True, identity_verified=False, correct_figures=True,
                                      no_mis_selling=True, polite_close=True))

    async def ask(_transcript):
        return fake

    monkeypatch.setattr(pc, "_ask_gemini", ask)
    monkeypatch.setattr(pc, "get_settings", lambda: type("S", (), {"google_api_key": "k"})())
    call = asyncio.run(pc.analyse_call(db, s.call_id))
    assert call.outcome == CallOutcome.payment_link_sent  # tool evidence beats the model
    qa = db.get(pc.QAScore, s.call_id)
    assert qa.identity_verified and qa.score == 5  # verified per system record, not the model
