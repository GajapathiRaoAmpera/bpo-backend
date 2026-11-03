"""Real WhatsApp sends via the Meta Cloud API. Only called when `whatsapp_enabled` (see app.core.config)."""

import re

import httpx

from app.core.config import get_settings
from app.models.enums import Language

GRAPH_URL = "https://graph.facebook.com/v23.0"
META_LANG = {Language.english: "en", Language.hindi: "hi", Language.tamil: "ta",
             Language.telugu: "te", Language.kannada: "kn", Language.malayalam: "ml"}


def _to(phone: str) -> str:
    return "91" + re.sub(r"\D", "", phone)[-10:]


def _post(body: dict) -> None:
    s = get_settings()
    resp = httpx.post(f"{GRAPH_URL}/{s.whatsapp_phone_number_id}/messages", json=body, timeout=15,
                      headers={"Authorization": f"Bearer {s.whatsapp_access_token}"})
    if resp.status_code >= 400:  # Meta's body says why (template not approved, wrong params/language...)
        raise RuntimeError(f"WhatsApp {resp.status_code}: {resp.text[:500]}")


def template_lang(language: Language | str | None) -> str:
    """Meta code for the customer's language if the template is approved in it, else English."""
    s = get_settings()
    if s.whatsapp_template_name == "hello_world":
        return "en_US"
    code = META_LANG.get(language, "en")
    return code if code in {c.strip() for c in s.whatsapp_template_langs.split(",")} else "en"


def send_template(phone: str, params: list[str], language: Language | str | None = None) -> None:
    """Sends the configured approved template to `phone` (Indian 10-digit number). Raises on any failure;
    callers decide whether to retry. `params` fill the template's body placeholders in order; pass [] for a
    template with none (e.g. Meta's "hello_world" connectivity test)."""
    s = get_settings()
    body = {"messaging_product": "whatsapp", "to": _to(phone), "type": "template",
            "template": {"name": s.whatsapp_template_name, "language": {"code": template_lang(language)}}}
    if params:
        body["template"]["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]
    _post(body)


def send_text(phone: str, text: str) -> None:
    """Free-form text. Meta only delivers it inside the 24 h window after the customer last messaged us
    (and doesn't charge for it there), so use it only to reply to an inbound message."""
    _post({"messaging_product": "whatsapp", "to": _to(phone), "type": "text", "text": {"body": text[:4096]}})
