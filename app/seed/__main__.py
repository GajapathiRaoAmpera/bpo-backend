"""`python -m app.seed`: drop every table, recreate, and load the synthetic sandbox.

Refuses when APP_ENV=production unless `--i-know` is passed (once, on a fresh staging DB)."""

import sys
import time

from app.core.config import get_settings
from app.core.db import Base, SessionLocal, engine
from app.seed import seed

if __name__ == "__main__":
    if get_settings().app_env == "production" and "--i-know" not in sys.argv:
        sys.exit("APP_ENV=production: seeding drops every table. Re-run with --i-know if that is really intended.")
    t0 = time.perf_counter()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db, get_settings().seed_password)
    print(f"Sandbox reset in {time.perf_counter() - t0:.1f}s")
