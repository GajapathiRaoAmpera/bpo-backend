"""Razorpay Payment Links (test mode is free). Only called when `razorpay_enabled`."""

import hashlib
import hmac

import httpx

from app.core.config import get_settings
from app.models import Customer, Policy

API = "https://api.razorpay.com/v1/payment_links"


def create_payment_link(policy: Policy, customer: Customer, reference: str) -> str:
    """Creates a link for the policy's premium and returns its short_url. Raises on any failure.
    `reference` must be unique per link (Razorpay rejects a reused reference_id)."""
    s = get_settings()
    resp = httpx.post(API, auth=(s.razorpay_key_id, s.razorpay_key_secret), timeout=15, json={
        "amount": policy.premium_inr * 100, "currency": "INR",  # paise
        "reference_id": reference[:40], "description": f"Renewal premium {policy.policy_no}"[:255],
        "customer": {"name": customer.full_name, "contact": "+91" + customer.phone[-10:]},
        "notify": {"sms": False, "email": False},  # we send it ourselves on WhatsApp
        "notes": {"policy_no": policy.policy_no},
    })
    resp.raise_for_status()
    return resp.json()["short_url"]


def valid_signature(body: bytes, signature: str) -> bool:
    secret = get_settings().razorpay_webhook_secret
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(secret) and hmac.compare_digest(signature, expected)


def payment_link_for(policy: Policy, customer: Customer, reference: str) -> str:
    """A real Razorpay link when configured, else the sandbox URL (not payable). Raises if Razorpay fails."""
    s = get_settings()
    if s.razorpay_enabled:
        return create_payment_link(policy, customer, reference)
    return f"{s.payment_link_base}/{reference.lower()}"
