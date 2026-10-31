from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.core.db import Base


def test_migrations_match_models(tmp_path):
    """Fails when a model changes without a migration: run `alembic revision --autogenerate -m ...`."""
    engine = create_engine(f"sqlite:///{tmp_path}/migrated.db")
    cfg = Config("alembic.ini")
    with engine.begin() as conn:
        cfg.attributes.update(connection=conn, configure_logger=False)
        command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []
