import csv
import io
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.db import utcnow
from app.models import AuditLog, Call, Customer, TranscriptTurn, User
from app.models.enums import AuditAction, CallDirection, CallMode, CallStatus, Role, Speaker

R = Role
AUDIT_ROLES = {R.super_admin, R.compliance_officer}
LIVE_ROLES = {R.team_lead, R.ops_manager}


@pytest.mark.parametrize("role", list(Role))
def test_audit_rbac(login, role):
    c = login(role)
    ok = role in AUDIT_ROLES
    assert c.get("/api/v1/audit").status_code == (200 if ok else 403)
    assert c.get("/api/v1/audit/export.csv").status_code == (200 if ok else 403)


@pytest.mark.parametrize("role", list(Role))
def test_live_rbac(login, role):
    assert login(role).get("/api/v1/live/calls").status_code == (200 if role in LIVE_ROLES else 403)


def test_phase5_reads_need_sign_in(client):
    for path in ("/api/v1/audit", "/api/v1/audit/export.csv", "/api/v1/live/calls"):
        assert client.get(path).status_code == 401


def test_audit_filters_and_pagination(login, db):
    c = login(R.compliance_officer)
    body = c.get("/api/v1/audit", params={"page_size": 5}).json()
    assert len(body["items"]) == 5 and body["total"] > 5
    logins = c.get("/api/v1/audit", params={"action": "login", "page_size": 100}).json()
    assert logins["items"] and {i["action"] for i in logins["items"]} == {"login"}
    mine = c.get("/api/v1/audit", params={"actor": "lakshmi", "entity_type": "user"}).json()["items"]
    assert mine and all(i["actor_name"] == "Lakshmi Venkatesh" and i["entity_type"] == "user" for i in mine)
    assert mine[0]["actor_name"] == "Lakshmi Venkatesh"
    future = (utcnow() + timedelta(days=3)).date().isoformat()
    assert c.get("/api/v1/audit", params={"date_from": future}).json()["total"] == 0
    today = utcnow().date().isoformat()
    assert c.get("/api/v1/audit", params={"date_from": today, "date_to": future}).json()["total"] > 0
    assert c.get("/api/v1/audit", params={"action": "nope"}).status_code == 422
    assert c.get("/api/v1/audit", params={"page_size": 500}).status_code == 422


def test_audit_export_is_csv_and_audited(login, db):
    c = login(R.super_admin)
    expected = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == AuditAction.login))
    r = c.get("/api/v1/audit/export.csv", params={"action": "login"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][:5] == ["id", "timestamp_utc", "actor", "actor_role", "action"]
    assert len(rows) - 1 == expected and all(row[4] == "login" for row in rows[1:])
    entry = db.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
    assert entry.action == AuditAction.export and entry.entity_type == "audit_log"
    assert entry.detail == {"format": "csv", "rows": expected, "filters": {"action": "login"}}
    assert entry.actor_role == R.super_admin


def test_live_calls_lists_in_progress_with_masking(login, db):
    cust = db.scalars(select(Customer)).first()
    agent = db.scalar(select(User).where(User.role == R.human_agent))
    call = Call(call_ref="CALL-LIVE-TEST-0001", direction=CallDirection.outbound, mode=CallMode.renewal,
                customer_id=cust.id, policy_id=cust.policies[0].id, agent_user_id=agent.id,
                status=CallStatus.in_progress, started_at=utcnow() - timedelta(seconds=90))
    db.add(call)
    db.flush()
    db.add_all([TranscriptTurn(call_id=call.id, seq=1, speaker=Speaker.bot, text="This call is recorded.", offset_ms=0),
                TranscriptTurn(call_id=call.id, seq=2, speaker=Speaker.customer, text="My DOB is 1 Jan 1980", offset_ms=5000)])
    db.commit()
    try:
        tl = {r["call_id"]: r for r in login(R.team_lead).get("/api/v1/live/calls").json()}[call.id]
        assert tl["customer_phone"] == cust.phone and tl["agent_name"] == agent.full_name
        assert tl["last_text"] == "My DOB is 1 Jan 1980" and tl["duration_sec"] >= 90
        assert tl["taken_over"] is False and tl["bot_connected"] is False

        om = {r["call_id"]: r for r in login(R.ops_manager).get("/api/v1/live/calls").json()}[call.id]
        assert om["customer_phone"] != cust.phone and "XXXX" in om["customer_phone"]
        assert om["last_text"] == "This call is recorded."  # customer speech hidden from masked roles
    finally:
        call.status, call.ended_at = CallStatus.completed, utcnow()
        db.commit()
    ids = [r["call_id"] for r in login(R.team_lead).get("/api/v1/live/calls").json()]
    assert call.id not in ids
