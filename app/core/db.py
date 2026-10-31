from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings


class UTCDateTime(TypeDecorator):
    """Stores UTC; always returns tz-aware UTC (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to UTCDateTime")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime}

def make_engine(url: str):
    is_sqlite = url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {
        "connect_timeout": 10,                       # give up connecting after 10 s
        "keepalives": 1, "keepalives_idle": 30,      # spot a dead network link
        "keepalives_interval": 10, "keepalives_count": 3,
    }
    engine = create_engine(url, connect_args=connect_args,
                           pool_pre_ping=not is_sqlite, pool_timeout=30)  # Supabase pooler drops idle connections
    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    else:
        @event.listens_for(engine, "connect")
        def _statement_timeout(dbapi_conn, _):
            # Supabase's pooler ignores the `options` startup parameter, so SET it per connection;
            # commit so SQLAlchemy's reset-on-return rollback doesn't undo it.
            dbapi_conn.execute("SET statement_timeout = 30000")  # cancel any query running > 30 s
            dbapi_conn.commit()
    return engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    # app.state.session_factory lets tests and the audit middleware share one DB
    with request.app.state.session_factory() as db:
        yield db
