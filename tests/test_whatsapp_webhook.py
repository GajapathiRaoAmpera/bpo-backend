"""Meta WhatsApp webhook: GET verification handshake, POST signature check."""

import hashlib
import hmac

import pytest

from app.core.config import get_settings

API = "/api/v1/whatsapp/webhook"
SECRET = "app-secret"
VERIFY_TOKEN = "verify-me"


@pytest.fixture
def webhook_on(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", VERIFY_TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_handshake_echoes_challenge_with_correct_token(client, webhook_on):
    r = client.get(API, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "123"})
    assert r.status_code == 200 and r.text == "123"


def test_handshake_rejects_wrong_token(client, webhook_on):
    r = client.get(API, params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "123"})
    assert r.status_code == 403


def test_post_accepts_a_correctly_signed_event(client, webhook_on):
    body = b'{"entry": []}'
    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    r = client.post(API, content=body, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    assert r.status_code == 200


def test_post_rejects_a_bad_signature(client, webhook_on):
    r = client.post(API, content=b'{"entry": []}', headers={"X-Hub-Signature-256": "sha256=bad"})
    assert r.status_code == 403
