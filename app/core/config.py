from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["sandbox", "production"] = "sandbox"
    # Fallback link base when Razorpay isn't configured (sandbox: links are not payable).
    payment_link_base: str = "https://pay.sandbox.amperabpo.in/r"
    jwt_secret: str
    seed_password: str
    database_url: str = "sqlite:///./amperabpo.db"
    frontend_origin: str = "http://localhost:3000"
    google_api_key: str = ""
    cookie_secure: bool = False
    gemini_live_model: str = "models/gemini-3.8-live"
    gemini_live_voice: str = "Kore"
    gemini_flash_model: str = "gemini-3.8-flash"
    # Tried in order when the primary returns 503/504 under peak demand.
    gemini_flash_fallback_models: str = "gemini-3.5-flash,gemini-flash-latest"

    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 8 * 60
    cookie_name: str = "av_session"
    login_rate_limit: str = "5/minute"  # per client IP, slowapi syntax

    # Real phone calls (Plivo). Numbers without "+", e.g. 918031234567. Unset = campaigns simulate calls.
    plivo_auth_id: str = ""
    plivo_auth_token: str = ""
    plivo_from_number: str = ""
    public_base_url: str = ""  # backend's public https URL that Plivo calls back, no trailing slash
    agent_transfer_number: str = ""  # human line that escalated phone calls transfer to, e.g. 919876543210
    contact_allowlist_prefix: str = ""  # e.g. "TST-": only customers whose code starts with it are dialled/messaged

    # Real WhatsApp sends (Meta Cloud API). Unset = follow-ups store the message but send nothing.
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_template_name: str = ""  # must be an approved template in Meta Business Manager
    whatsapp_callback_template: str = ""  # approved template for callback follow-ups; unset = recorded only
    whatsapp_reminder_template: str = ""  # approved template for renewal reminders; unset = recorded only
    whatsapp_app_secret: str = ""  # Meta app's Basic Settings; verifies X-Hub-Signature-256 on the webhook
    whatsapp_webhook_verify_token: str = ""  # any string you choose; Meta echoes it back on the GET handshake
    whatsapp_template_langs: str = "en"  # Meta language codes the template is approved in, e.g. "en,hi,ta"

    # Real payment links (Razorpay; test-mode keys are free). Unset = sandbox links that can't be paid.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""  # the secret you typed when adding the webhook in the dashboard

    @property
    def plivo_enabled(self) -> bool:
        return all((self.plivo_auth_id, self.plivo_auth_token, self.plivo_from_number, self.public_base_url))

    @property
    def whatsapp_enabled(self) -> bool:
        return all((self.whatsapp_phone_number_id, self.whatsapp_access_token, self.whatsapp_template_name))

    @property
    def razorpay_enabled(self) -> bool:
        return all((self.razorpay_key_id, self.razorpay_key_secret))

    @model_validator(mode="after")
    def _production_guard(self) -> "Settings":
        # Refuse to start production with sandbox-grade settings.
        if self.app_env == "production":
            problems = [msg for bad, msg in (
                (len(self.jwt_secret) < 32, "JWT_SECRET must be at least 32 characters"),
                (not self.cookie_secure, "COOKIE_SECURE must be true"),
                (self.database_url.startswith("sqlite"), "DATABASE_URL must not be SQLite"),
            ) if bad]
            if problems:
                raise ValueError("APP_ENV=production: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
