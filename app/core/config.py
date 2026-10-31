from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["sandbox", "production"] = "sandbox"
    # Payments integration is still pilot: links are built here but nothing is payable yet.
    payment_link_base: str = "https://pay.sandbox.assurevoice.in/r"
    jwt_secret: str
    seed_password: str
    database_url: str = "sqlite:///./assurevoice.db"
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
    contact_allowlist_prefix: str = ""  # e.g. "TST-": only customers whose code starts with it are dialled

    @property
    def plivo_enabled(self) -> bool:
        return all((self.plivo_auth_id, self.plivo_auth_token, self.plivo_from_number, self.public_base_url))

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
