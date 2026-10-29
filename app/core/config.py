from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jwt_secret: str
    seed_password: str
    database_url: str = "sqlite:///./assurevoice.db"
    frontend_origin: str = "http://localhost:3000"
    google_api_key: str = ""
    cookie_secure: bool = False

    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 8 * 60
    cookie_name: str = "av_session"


@lru_cache
def get_settings() -> Settings:
    return Settings()
