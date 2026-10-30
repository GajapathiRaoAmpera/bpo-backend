import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Call, Campaign, CampaignTarget
from app.models.enums import AuditAction, CallMode, CampaignStatus, Role
from app.services.scripts import active_script

R = Role
API = "/api/v1"
SCRIPT_OK = {k: True for k in ("ai_disclosure", "verification_before_details", "figures_from_tools_only",
                               "no_mis_selling", "escalation_path")}
CAMPAIGN_OK = {k: True for k in ("calling_window", "dnd_scrubbed", "approved_script", "segment_reviewed")}
PROMPT = ("You are Vani, an automated AI assistant; the call is recorded. Call verify_customer first. "
          "Never state a figure a tool did not return. No mis-selling. Use escalate_to_human when asked.")


def last_audit(db, action):
    return db.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id.desc())).first()


@pytest.mark.parametrize("role", list(Role))
def test_read_access_per_matrix(login, role):
    c = login(role)
    assert c.get(f"{API}/campaigns").status_code == (200 if role in {R.campaign_manager, R.ops_manager, R.compliance_officer} else 403)
    assert c.get(f"{API}/scripts").status_code == (200 if role in {R.bot_designer, R.compliance_officer} else 403)
    assert c.get(f"{API}/approvals").status_code == (200 if role in {R.ops_manager, R.compliance_officer} else 403)
    assert c.get(f"{API}/qa/queue").status_code == (200 if role in {R.qa_analyst, R.ops_manager, R.team_lead, R.compliance_officer} else 403)


def test_unauthenticated_401(client):
    for path in ("/campaigns", "/scripts", "/approvals", "/qa/queue"):
        assert client.get(API + path).status_code == 401


def test_viewers_and_makers_cannot_write_or_approve(login, db):
    pending = db.scalar(select(Campaign).where(Campaign.status == CampaignStatus.pending))
    assert login(R.compliance_officer).post(f"{API}/scripts", json={"name": "x" * 5, "mode": "inbound", "system_prompt": PROMPT}).status_code == 403
    assert login(R.compliance_officer).post(f"{API}/campaigns", json={"name": "Sample", "product": "motor"}).status_code == 403
    cm = login(R.campaign_manager)
    assert cm.post(f"{API}/campaigns/{pending.id}/approve", json={"checklist": CAMPAIGN_OK}).status_code == 403
    bd = login(R.bot_designer)
    assert bd.post(f"{API}/scripts/1/approve", json={"checklist": SCRIPT_OK}).status_code == 403


def test_script_lifecycle_switches_bot_after_approval(login, db):
    bd = login(R.bot_designer)
    assert bd.post(f"{API}/scripts", json={"name": "ab", "mode": "inbound", "system_prompt": PROMPT}).status_code == 422
    r = bd.post(f"{API}/scripts", json={"name": "Inbound service v-next", "mode": "inbound", "system_prompt": PROMPT})
    assert r.status_code == 201, r.text
    s = r.json()
    old_active = active_script(db, CallMode.inbound)
    assert s["status"] == "draft" and s["parent_id"] == old_active.id and s["version"] > old_active.version
    assert last_audit(db, AuditAction.create).entity_id == str(s["id"])

    assert bd.patch(f"{API}/scripts/{s['id']}", json={"system_prompt": PROMPT + " Close politely."}).status_code == 200
    test = bd.post(f"{API}/scripts/{s['id']}/test-call", json={"customer_says": "When is my renewal?"}).json()
    assert test["simulated"] and all(test["checks"].values()) and len(test["turns"]) == 3

    assert bd.post(f"{API}/scripts/{s['id']}/submit").status_code == 200
    assert last_audit(db, AuditAction.submit).entity_id == str(s["id"])
    assert bd.patch(f"{API}/scripts/{s['id']}", json={"name": "Too late"}).status_code == 409  # draft only

    om = login(R.ops_manager)
    item = next(a for a in om.get(f"{API}/approvals").json()["scripts"] if a["script"]["id"] == s["id"])
    assert item["base"]["id"] == old_active.id and any(d["tag"] != "equal" for d in item["diff"])
    db.expire_all()
    assert active_script(db, CallMode.inbound).id == old_active.id  # pending never reaches the bot

    assert om.post(f"{API}/scripts/{s['id']}/approve", json={"checklist": {**SCRIPT_OK, "no_mis_selling": False}}).status_code == 422
    r = om.post(f"{API}/scripts/{s['id']}/approve", json={"checklist": SCRIPT_OK, "note": "Meets disclosure rules."})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["active"]
    db.expire_all()
    assert active_script(db, CallMode.inbound).id == s["id"]
    assert last_audit(db, AuditAction.approve).entity_id == str(s["id"])
    assert om.post(f"{API}/scripts/{s['id']}/approve", json={"checklist": SCRIPT_OK}).status_code == 409


def test_script_reject_needs_note(login, db):
    bd = login(R.bot_designer)
    s = bd.post(f"{API}/scripts", json={"name": "Renewal tone tweak", "mode": "renewal", "system_prompt": PROMPT}).json()
    bd.post(f"{API}/scripts/{s['id']}/submit")
    co = login(R.compliance_officer)
    assert co.post(f"{API}/scripts/{s['id']}/reject", json={}).status_code == 422
    r = co.post(f"{API}/scripts/{s['id']}/reject", json={"note": "Add the grace-period wording."})
    assert r.json()["status"] == "rejected"
    assert last_audit(db, AuditAction.reject).entity_id == str(s["id"])


def test_self_approval_blocked(login, db):
    # Makers lack the approve role, so plant an item "created" by the checker to prove the explicit check.
    om = login(R.ops_manager)
    me = om.get(f"{API}/auth/me").json()["user"]["id"]
    c = db.scalar(select(Campaign).where(Campaign.status == CampaignStatus.pending))
    original = c.created_by
    c.created_by = me
    db.commit()
    try:
        r = om.post(f"{API}/campaigns/{c.id}/approve", json={"checklist": CAMPAIGN_OK})
        assert r.status_code == 403 and "you created" in r.json()["detail"]
    finally:
        c.created_by = original
        db.commit()


def test_campaign_wizard_approve_launch(login, db):
    cm = login(R.campaign_manager)
    preview = cm.get(f"{API}/campaigns/audience", params={"product": "motor", "renewal_within_days": 60}).json()
    assert preview["total_targets"] > 0 and preview["script"]
    r = cm.post(f"{API}/campaigns", json={"name": "Motor renewals · next 60 days", "product": "motor",
                                          "segment": {"renewal_within_days": 60, "languages": ["english", "tamil", "hindi"]}})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] == "draft" and c["total_targets"] == preview["total_targets"]
    assert cm.post(f"{API}/campaigns/{c['id']}/launch").status_code == 409  # not approved

    r = cm.patch(f"{API}/campaigns/{c['id']}", json={"segment": {"renewal_within_days": 60, "languages": ["english"]}})
    assert r.status_code == 200 and r.json()["total_targets"] <= c["total_targets"]
    assert r.json()["total_targets"] == db.scalar(select(func.count()).select_from(CampaignTarget).where(CampaignTarget.campaign_id == c["id"]))
    assert cm.post(f"{API}/campaigns/{c['id']}/submit").status_code == 200
    assert cm.patch(f"{API}/campaigns/{c['id']}", json={"name": "Changed"}).status_code == 409

    co = login(R.compliance_officer)
    assert any(x["id"] == c["id"] for x in co.get(f"{API}/approvals").json()["campaigns"])
    assert co.post(f"{API}/campaigns/{c['id']}/approve", json={"checklist": CAMPAIGN_OK}).json()["status"] == "approved"

    cm = login(R.campaign_manager)
    r = cm.post(f"{API}/campaigns/{c['id']}/launch")
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["call_ids"]) == 3 and out["campaign"]["status"] == "running"
    calls = db.scalars(select(Call).where(Call.id.in_(out["call_ids"]))).all()
    assert all(x.is_simulated and x.campaign_id == c["id"] and x.ended_at for x in calls)
    camp = out["campaign"]
    assert camp["dialled"] == 3
    assert camp["connected"] == sum(x.outcome != "no_answer" for x in calls)
    assert camp["converted"] == sum(x.outcome in ("renewed", "payment_link_sent") for x in calls)
    assert last_audit(db, AuditAction.launch).entity_id == str(c["id"])
