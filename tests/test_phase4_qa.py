from sqlalchemy import select

from app.core.formatting import format_date
from app.models import AuditLog, Customer
from app.models.enums import AuditAction, Role

API = "/api/v1/qa"
ALL_PASS = {"ai_disclosure": True, "identity_verified": True, "correct_figures": True,
            "no_mis_selling": True, "polite_close": False}


def test_queue_lists_open_reviews(login):
    page = login(Role.qa_analyst).get(f"{API}/queue").json()
    assert page["total"] > 0 and all(r["locked_at"] is None for r in page["items"])
    locked = login(Role.qa_analyst).get(f"{API}/queue", params={"state": "locked"}).json()
    assert all(r["locked_at"] for r in locked["items"])


def test_override_lock_and_locked_is_read_only(login, db):
    qa = login(Role.qa_analyst)
    call_id = qa.get(f"{API}/queue").json()["items"][0]["call_id"]
    detail = qa.get(f"{API}/{call_id}").json()
    assert detail["turns"]

    assert login(Role.ops_manager).put(f"{API}/{call_id}", json=ALL_PASS).status_code == 403  # view only
    qa = login(Role.qa_analyst)
    assert qa.put(f"{API}/{call_id}", json={"ai_disclosure": True}).status_code == 422
    r = qa.put(f"{API}/{call_id}", json={**ALL_PASS, "note": "Closed without next steps."})
    assert r.status_code == 200 and r.json()["score"] == 4 and r.json()["source"] == "human" and not r.json()["locked_at"]
    r = qa.put(f"{API}/{call_id}", json={**ALL_PASS, "note": "Closed without next steps.", "lock": True})
    assert r.json()["locked_at"]
    assert qa.put(f"{API}/{call_id}", json=ALL_PASS).status_code == 409
    entry = db.scalars(select(AuditLog).where(AuditLog.entity_type == "qa_score").order_by(AuditLog.id.desc())).first()
    assert (entry.action, entry.entity_id, entry.detail["locked"]) == (AuditAction.update, str(call_id), True)


def test_transcript_hides_date_of_birth(login, db):
    qa = login(Role.qa_analyst)
    for row in qa.get(f"{API}/queue", params={"state": "all", "page_size": 20}).json()["items"]:
        d = qa.get(f"{API}/{row['call_id']}").json()
        c = db.scalar(select(Customer).where(Customer.full_name == d["customer_name"]))
        if c:
            assert all(format_date(c.dob) not in t["text"] for t in d["turns"])
