"""`python -m app.seed`: drop every table, recreate, and load the synthetic sandbox."""

import time

from app.core.config import get_settings
from app.core.db import Base, SessionLocal, engine
from app.seed import seed

if __name__ == "__main__":
    t0 = time.perf_counter()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db, get_settings().seed_password)
    print(f"Sandbox reset in {time.perf_counter() - t0:.1f}s")
