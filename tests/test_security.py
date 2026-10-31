import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.config import Settings
from app.core.ratelimit import limiter
from app.core.security import hash_password
from app.models import User
from app.models.enums import Role
from tests.conftest import EMAILS, PASSWORD
from tests.test_app_env import GOOD


@pytest.fixture
def limited():
    limiter.reset()
    limiter.enabled = True
    yield
    limiter.enabled = False
    limiter.reset()


def test_sixth_login_from_same_ip_in_a_minute_is_429(client, limited):
    ip = {"X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
    body = {"email": "nobody@sandbox.assurevoice.in", "password": "wrong"}
    codes = [client.post("/api/v1/auth/login", json=body, headers=ip).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]
    # A different client IP is unaffected.
    other = client.post("/api/v1/auth/login", json={"email": EMAILS[Role.ops_manager], "password": PASSWORD},
                        headers={"X-Forwarded-For": "198.51.100.9"})
    assert other.status_code == 200


def test_locked_email_rejects_even_the_right_password(client, db):
    email = "lockout.victim@sandbox.assurevoice.in"
    db.add(User(email=email, full_name="Lockout Victim", job_title="Agent", role=Role.human_agent,
                password_hash=hash_password(PASSWORD)))
    db.commit()
    for _ in range(10):
        assert client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 401 and "av_session" not in r.cookies


def test_security_headers_present(client):
    h = client.get("/health").headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "strict-transport-security" not in h  # COOKIE_SECURE is off in tests
    assert client.get("/openapi.json").status_code == 200


def test_production_hides_docs_and_sends_hsts(monkeypatch):
    monkeypatch.setattr(main, "get_settings", lambda: Settings(_env_file=None, **GOOD))
    c = TestClient(main.create_app())  # no lifespan needed for these routes
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404
    assert c.get("/health").headers["strict-transport-security"].startswith("max-age=")
