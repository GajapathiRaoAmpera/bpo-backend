"""Migrations run against DATABASE_URL (app settings), or a connection passed in by tests."""

from logging.config import fileConfig

from alembic import context

import app.models  # noqa: F401  (registers tables on Base.metadata)
from app.core.config import get_settings
from app.core.db import Base, UTCDateTime, make_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)


def render_item(type_, obj, autogen_context):
    # Migrations shouldn't import app code: UTCDateTime is a tz-aware DateTime in the database.
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run(connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, render_item=render_item,
                      render_as_batch=connection.dialect.name == "sqlite")  # SQLite can't ALTER most things
    with context.begin_transaction():
        context.run_migrations()


if (conn := config.attributes.get("connection")) is not None:
    run(conn)
else:
    with make_engine(get_settings().database_url).connect() as conn:
        run(conn)
