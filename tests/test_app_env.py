import pytest
from pydantic import ValidationError

from app.core.config import Settings

GOOD = dict(app_env="production", jwt_secret="x" * 32, seed_password="p", cookie_secure=True,
            database_url="postgresql+psycopg://u:p@db/postgres")


def test_production_accepts_safe_settings():
    assert Settings(_env_file=None, **GOOD).app_env == "production"


@pytest.mark.parametrize("override, message", [
    ({"jwt_secret": "short"}, "JWT_SECRET"),
    ({"cookie_secure": False}, "COOKIE_SECURE"),
    ({"database_url": "sqlite:///./x.db"}, "SQLite"),
])
def test_production_refuses_unsafe_settings(override, message):
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **{**GOOD, **override})


def test_sandbox_allows_dev_settings():
    Settings(_env_file=None, jwt_secret="short", seed_password="p", database_url="sqlite:///./x.db")


def test_seed_refuses_production_without_flag():
    import os
    import subprocess
    import sys
    env = {**os.environ, "APP_ENV": "production", "JWT_SECRET": "x" * 32, "COOKIE_SECURE": "true",
           "DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:1/none"}  # never reached: the guard exits first
    r = subprocess.run([sys.executable, "-m", "app.seed"], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 1 and "--i-know" in r.stderr
