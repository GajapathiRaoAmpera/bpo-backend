import pytest
from sqlalchemy import select

from app.models import AuditLog, User
from app.models.enums import AuditAction, Role

R = Role
NEW = {"email": "Nila.Ramesh@sandbox.assurevoice.in", "full_name": "Nila Ramesh",
       "job_title": "Service Executive", "role": "human_agent", "password": "Fresh@Pass9"}


def _last(db) -> AuditLog:
    return db.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("path", ["/api/v1/users", "/api/v1/integrations"])
def test_admin_endpoints_super_admin_only(login, role, path):
    assert login(role).get(path).status_code == (200 if role == R.super_admin else 403)


def test_admin_endpoints_need_sign_in(client):
    assert client.get("/api/v1/users").status_code == 401
    assert client.post("/api/v1/users", json=NEW).status_code == 401


def test_integrations_list(login):
    rows = login(R.super_admin).get("/api/v1/integrations").json()
    assert {r["status"] for r in rows} == {"connected", "pilot"}
    assert any(r["key"] == "whatsapp" and r["status"] == "pilot" for r in rows)


def test_user_lifecycle(login, client, db):
    c = login(R.super_admin)
    assert c.post("/api/v1/users", json={**NEW, "password": "short"}).status_code == 422
    r = c.post("/api/v1/users", json=NEW)
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert r.json()["email"] == NEW["email"].lower() and "password" not in r.json()
    assert (_last(db).action, _last(db).entity_id) == (AuditAction.create, str(uid))
    stored = db.get(User, uid)
    assert stored.password_hash != NEW["password"]
    assert c.post("/api/v1/users", json=NEW).status_code == 409

    r = c.patch(f"/api/v1/users/{uid}", json={"role": "team_lead", "password": "Other@Pass9"})
    assert r.status_code == 200 and r.json()["role"] == "team_lead"
    entry = _last(db)
    assert entry.action == AuditAction.update and entry.detail["password_reset"] is True
    assert "Other@Pass9" not in str(entry.detail)

    r = c.delete(f"/api/v1/users/{uid}")
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (_last(db).action, _last(db).entity_id) == (AuditAction.delete, str(uid))
    db.expire_all()
    assert db.get(User, uid) is not None  # deactivated, never deleted

    r = client.post("/api/v1/auth/login", json={"email": NEW["email"], "password": "Other@Pass9"})
    assert r.status_code == 401

    # reactivate: the new password now works
    login(R.super_admin).patch(f"/api/v1/users/{uid}", json={"is_active": True})
    assert client.post("/api/v1/auth/login", json={"email": NEW["email"], "password": "Other@Pass9"}).status_code == 200


def test_cannot_deactivate_or_demote_self(login, db):
    c = login(R.super_admin)
    me = c.get("/api/v1/auth/me").json()["user"]["id"]
    assert c.delete(f"/api/v1/users/{me}").status_code == 400
    assert c.patch(f"/api/v1/users/{me}", json={"is_active": False}).status_code == 400
    assert c.patch(f"/api/v1/users/{me}", json={"role": "qa_analyst"}).status_code == 400
    assert db.get(User, me).is_active
    assert c.delete("/api/v1/users/999999").status_code == 404
