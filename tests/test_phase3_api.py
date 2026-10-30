import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.db import utcnow
from app.core.formatting import format_date
from app.core.masking import mask_phone
from app.models import AuditLog, Call, Customer, Escalation, Followup, Policy, User
from app.models.enums import AuditAction, EscalationStatus, FollowupStatus, Role

R = Role
API = "/api/v1"
MASKED_PHONE = re.compile(r"^\d{2}X{6}\d{2}$")


def _last_audit(db) -> AuditLog:
    db.expire_all()
    return db.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()


def _open_escalation(db) -> Escalation:
    cid = db.scalar(select(Customer.id))
    e = Escalation(customer_id=cid, reason="Disputed late fee", priority="high")
    db.add(e)
    db.commit()
    return e


def _uid(db, role) -> int:
    return db.scalar(select(User.id).where(User.role == role))


# --- RBAC ---------------------------------------------------------------------

READ_ROLES = {
    "/dashboard/summary": {R.super_admin, R.ops_manager, R.team_lead, R.campaign_manager, R.qa_analyst, R.compliance_officer},
    "/dashboard/trends": {R.super_admin, R.ops_manager, R.team_lead, R.campaign_manager, R.qa_analyst, R.compliance_officer},
    "/calls": set(Role),
    "/escalations": {R.team_lead, R.human_agent, R.ops_manager},
    "/followups": {R.team_lead, R.human_agent, R.campaign_manager, R.ops_manager},
}


@pytest.mark.parametrize("path", list(READ_ROLES))
@pytest.mark.parametrize("role", list(Role))
def test_read_endpoints_rbac(login, role, path):
    assert login(role).get(API + path).status_code == (200 if role in READ_ROLES[path] else 403)


def test_unauthenticated_is_401(client):
    for path in [*READ_ROLES, "/policies/MOT-2026-000001", "/calls/1"]:
        assert client.get(API + path).status_code == 401


@pytest.mark.parametrize("role", [R.ops_manager, R.human_agent])  # OM may view but not act
def test_escalation_and_followup_writes_rbac(login, db, role):
    e = _open_escalation(db).id
    c = login(role)
    expected = 403 if role == R.ops_manager else 200
    assert c.post(f"{API}/escalations/{e}/claim").status_code == expected
    pid = db.scalar(select(Policy.id))
    r = c.post(f"{API}/followups", json={"policy_id": pid, "kind": "callback", "reason": "Customer asked",
                                         "due_at": (utcnow() + timedelta(hours=2)).isoformat()})
    assert r.status_code == (403 if role == R.ops_manager else 201)


# --- dashboard ------------------------------------------------------------------

def test_dashboard_summary_and_trends(login):
    c = login(R.ops_manager)
    s = c.get(f"{API}/dashboard/summary").json()
    assert s["calls"] > 0 and s["connected"] <= s["calls"] and 0 <= s["ai_contained_pct"] <= 100
    assert sum(s["sentiment"].values()) <= s["calls"]
    t = c.get(f"{API}/dashboard/trends", params={"days": 10}).json()
    assert len(t) == 10
    month = c.get(f"{API}/dashboard/trends").json()
    assert sum(p["calls"] for p in month) == s["calls"] and sum(p["renewals"] for p in month) == s["renewals"]
    assert sum(p["collections_inr"] for p in t) >= 0
    assert c.get(f"{API}/dashboard/trends", params={"days": 500}).status_code == 422


# --- policies -------------------------------------------------------------------

def test_policy_masked_and_audited(login, db):
    p = db.scalars(select(Policy)).first()
    cust = db.get(Customer, p.customer_id)
    r = login(R.compliance_officer).get(f"{API}/policies/{p.policy_no.lower()}")
    assert r.status_code == 200
    body = r.json()
    assert body["customer"]["phone"] == mask_phone(cust.phone) and body["customer"]["dob"] is None
    assert cust.email not in r.text
    last = _last_audit(db)
    assert (last.action, last.entity_type, last.entity_id) == (AuditAction.view_customer, "policy", p.policy_no)
    full = login(R.team_lead).get(f"{API}/policies/{p.policy_no}").json()
    assert full["customer"]["phone"] == cust.phone
    assert login(R.bot_designer).get(f"{API}/policies/{p.policy_no}").status_code == 403
    assert login(R.team_lead).get(f"{API}/policies/MOT-0000-000000").status_code == 404


# --- calls ----------------------------------------------------------------------

def test_human_agent_sees_only_own_calls(login, db):
    divya = _uid(db, R.human_agent)
    c = login(R.human_agent)
    items = c.get(f"{API}/calls", params={"page_size": 100}).json()["items"]
    own = set(db.scalars(select(Call.id).where(Call.agent_user_id == divya)))
    assert {i["id"] for i in items} <= own and items
    other = db.scalar(select(Call.id).where((Call.agent_user_id != divya) | Call.agent_user_id.is_(None)))
    assert c.get(f"{API}/calls/{other}").status_code == 404
    assert c.get(f"{API}/calls/{items[0]['id']}").status_code == 200


def test_call_detail_masked_for_view_roles_and_audited(login, db):
    call = db.scalars(select(Call).where(Call.customer_id.is_not(None), Call.verified)).first()
    cust = db.get(Customer, call.customer_id)
    full = login(R.team_lead).get(f"{API}/calls/{call.id}").text
    assert format_date(cust.dob) in full  # the seeded transcript does contain the spoken DOB
    r = login(R.qa_analyst).get(f"{API}/calls/{call.id}")
    assert r.status_code == 200
    d = r.json()
    assert MASKED_PHONE.match(d["customer"]["phone"]) and d["customer"]["dob"] is None and d["turns"]
    assert format_date(cust.dob) not in r.text and cust.dob.isoformat() not in r.text and cust.email not in r.text
    last = _last_audit(db)
    assert (last.action, last.entity_type, last.entity_id) == (AuditAction.view_customer, "call", str(call.id))
    assert login(R.team_lead).get(f"{API}/calls/{call.id}").json()["customer"]["phone"] == cust.phone


def test_calls_filters(login):
    c = login(R.ops_manager)
    all_ = c.get(f"{API}/calls").json()
    esc = c.get(f"{API}/calls", params={"outcome": "escalated"}).json()
    assert 0 < esc["total"] < all_["total"] and all(i["outcome"] == "escalated" for i in esc["items"])
    ref = all_["items"][0]["call_ref"]
    assert c.get(f"{API}/calls", params={"q": ref}).json()["items"][0]["call_ref"] == ref
    assert c.get(f"{API}/calls", params={"outcome": "bogus"}).status_code == 422


def test_customer_360_includes_calls_and_followups(login, db):
    f = db.scalars(select(Followup)).first()
    body = login(R.team_lead).get(f"{API}/customers/{f.customer_id}").json()
    assert any(x["id"] == f.id for x in body["followups"]) and "calls" in body


# --- escalations -----------------------------------------------------------------

def test_claim_and_resolve(login, db):
    e = _open_escalation(db)
    tl = login(R.team_lead)
    r = tl.post(f"{API}/escalations/{e.id}/claim")
    assert r.status_code == 200 and r.json()["status"] == "assigned" and r.json()["assigned_to_name"]
    assert _last_audit(db).detail == {"action": "claim"}
    ha = login(R.human_agent)
    assert ha.post(f"{API}/escalations/{e.id}/claim").status_code == 409  # someone else has it
    assert ha.post(f"{API}/escalations/{e.id}/resolve", json={"note": "Done"}).status_code == 409
    tl = login(R.team_lead)
    assert tl.post(f"{API}/escalations/{e.id}/resolve", json={"note": ""}).status_code == 422
    r = tl.post(f"{API}/escalations/{e.id}/resolve", json={"note": "Explained the charges."})
    assert r.status_code == 200 and r.json()["status"] == "resolved" and r.json()["resolved_at"]
    last = _last_audit(db)
    assert (last.action, last.entity_id, last.detail) == (AuditAction.update, str(e.id), {"action": "resolve"})
    assert tl.post(f"{API}/escalations/{e.id}/claim").status_code == 409
    assert e.id not in {x["id"] for x in tl.get(f"{API}/escalations").json()}


def test_escalation_queue_masks_phone_for_ops_manager(login):
    rows = login(R.ops_manager).get(f"{API}/escalations").json()
    assert rows and all(MASKED_PHONE.match(r["phone"]) for r in rows)
    ranks = ["urgent", "high", "medium", "low"]
    assert [ranks.index(r["priority"]) for r in rows] == sorted(ranks.index(r["priority"]) for r in rows)


# --- follow-ups --------------------------------------------------------------------

def test_followup_create_and_patch(login, db):
    pid = db.scalar(select(Policy.id).offset(3))
    c = login(R.campaign_manager)
    due = (utcnow() + timedelta(days=1)).isoformat()
    r = c.post(f"{API}/followups", json={"policy_id": pid, "kind": "reminder", "due_at": due, "reason": "Renewal reminder"})
    assert r.status_code == 201
    f = r.json()
    assert f["status"] == "scheduled" and f["created_by_name"]
    last = _last_audit(db)
    assert (last.action, last.entity_type, last.entity_id) == (AuditAction.create, "followup", str(f["id"]))

    assert c.post(f"{API}/followups", json={"policy_id": pid, "due_at": "2026-11-01T10:00:00",
                                            "reason": "naive"}).status_code == 422  # timezone required
    assert c.post(f"{API}/followups", json={"policy_id": 999999, "due_at": due, "reason": "x" * 5}).status_code == 404

    url = f"{API}/followups/{f['id']}"
    assert c.patch(url, json={}).status_code == 422
    assert c.patch(url, json={"status": "sent"}).status_code == 422
    r = c.patch(url, json={"reason": "Call after 5 pm"})
    assert r.status_code == 200 and r.json()["reason"] == "Call after 5 pm"
    assert _last_audit(db).action == AuditAction.update
    assert c.patch(url, json={"status": "cancelled"}).json()["status"] == "cancelled"
    assert c.patch(url, json={"status": "done"}).status_code == 409
    assert login(R.ops_manager).patch(url, json={"status": "done"}).status_code == 403


def test_followups_filter(login):
    items = login(R.ops_manager).get(f"{API}/followups", params={"status": "scheduled"}).json()["items"]
    assert all(i["status"] == "scheduled" for i in items)
    dues = [i["due_at"] for i in items]
    assert dues == sorted(dues)
