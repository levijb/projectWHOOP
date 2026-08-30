"""Explicitly gated Alembic entry point; never load .env or print a connection URL.

Tests inject a disposable SQLite Connection through Config.attributes["connection"]. CLI
execution requires WHOOP_PIPELINE_USE_POSTGRES plus DATABASE_URL, even for SQL-only output.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

from whoop_pipeline.storage.database import postgres_url, require_postgres_opt_in
from whoop_pipeline.storage.postgres_models import METADATA

config = context.config
target_metadata = METADATA


def configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="whoop_alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        if supplied_connection.dialect.name != "sqlite":
            raise RuntimeError("Injected migration connections are restricted to SQLite tests")
        configure(supplied_connection)
        return
    # URL objects avoid ConfigParser interpolation corrupting percent-encoded passwords.
    url = postgres_url(require_postgres_opt_in())
    if context.is_offline_mode():
        context.configure(
            url=url,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            version_table="whoop_alembic_version",
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(url, poolclass=pool.NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            configure(connection)
    finally:
        engine.dispose()


run_migrations()
