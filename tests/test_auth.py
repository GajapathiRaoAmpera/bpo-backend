import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.rbac import LANDING
from app.models import AuditLog
from app.models.enums import AuditAction, Role
from tests.conftest import EMAILS, PASSWORD


@pytest.mark.parametrize("role", list(Role))
def test_every_persona_logs_in_with_landing_and_menu(client, role):
    r = client.post("/api/v1/auth/login", json={"email": EMAILS[role], "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["role"] == role
    assert body["landing"] == LANDING[role]
    assert body["landing"] in {n["screen"] for n in body["nav"]}  # landing is always reachable
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/v1/auth/me").json()["user"]["email"] == EMAILS[role]


def test_wrong_password_is_401_and_audited(client, db):
    r = client.post("/api/v1/auth/login", json={"email": EMAILS[Role.ops_manager], "password": "nope"})
    assert r.status_code == 401
    assert get_settings().cookie_name not in r.cookies
    last = db.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
    assert last.action == AuditAction.login_failed


def test_unknown_email_is_401(client):
    r = client.post("/api/v1/auth/login", json={"email": "nobody@sandbox.assurevoice.in", "password": PASSWORD})
    assert r.status_code == 401


def test_no_cookie_or_bad_token_is_401(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.set(get_settings().cookie_name, "not-a-jwt")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_and_logout_are_audited(login, db):
    c = login(Role.compliance_officer)
    assert c.post("/api/v1/auth/logout").status_code == 204
    assert c.get("/api/v1/auth/me").status_code == 401
    actions = [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(2))]
    assert actions == [AuditAction.logout, AuditAction.login]
