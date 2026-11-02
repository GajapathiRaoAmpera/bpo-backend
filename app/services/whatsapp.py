"""Real WhatsApp sends via the Meta Cloud API. Only called when `whatsapp_enabled` (see app.core.config)."""

import re

import httpx

from app.core.config import get_settings

GRAPH_URL = "https://graph.facebook.com/v23.0"


def send_template(phone: str, params: list[str]) -> None:
    """Sends the configured approved template to `phone` (Indian 10-digit number). Raises on any failure;
    callers decide whether to retry. `params` fill the template's body placeholders in order; pass [] for a
    template with none (e.g. Meta's "hello_world" connectivity test)."""
    s = get_settings()
    to = "91" + re.sub(r"\D", "", phone)[-10:]
    body = {"messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": s.whatsapp_template_name, "language": {"code": "en_US"}}}
    if params:
        body["template"]["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]
    resp = httpx.post(f"{GRAPH_URL}/{s.whatsapp_phone_number_id}/messages", json=body, timeout=15,
                       headers={"Authorization": f"Bearer {s.whatsapp_access_token}"})
    resp.raise_for_status()
