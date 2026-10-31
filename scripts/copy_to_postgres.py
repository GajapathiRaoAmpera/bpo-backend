"""Copy a seeded SQLite sandbox into the Postgres in DATABASE_URL (e.g. Supabase).

    poetry run python -m scripts.copy_to_postgres [sqlite_path]

The seed inserts row by row, which is too slow and fragile over a remote connection, so seed
locally and bulk-copy instead. Drops and recreates every table in the target, like `make reset`.
"""

import sys

from sqlalchemy import create_engine, func, select, text

from app.core.db import Base, engine
import app.models  # noqa: F401  (registers tables on Base.metadata)

BATCH = 2000

if __name__ == "__main__":
    src = create_engine(f"sqlite:///{sys.argv[1] if len(sys.argv) > 1 else 'assurevoice.db'}")
    assert engine.dialect.name == "postgresql", "DATABASE_URL must point at Postgres"
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with src.connect() as s, engine.begin() as d:
        for table in Base.metadata.sorted_tables:  # parents before children (FKs)
            rows = [dict(r._mapping) for r in s.execute(select(table))]
            for i in range(0, len(rows), BATCH):
                d.execute(table.insert(), rows[i:i + BATCH])
            if "id" in table.c and rows:  # ids were copied explicitly; move the sequence past them
                d.execute(text(f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), :n)"),
                          {"n": max(r["id"] for r in rows)})
            n = d.execute(select(func.count()).select_from(table)).scalar()
            assert n == len(rows), f"{table.name}: copied {n} of {len(rows)}"
            print(f"{table.name}: {n}")
