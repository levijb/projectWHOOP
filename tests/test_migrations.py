"""Real Alembic revisions on SQLite; Postgres DDL is generated without opening a socket."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from whoop_pipeline.storage.postgres_backend import PostgresBackend
from whoop_pipeline.storage.postgres_models import METADATA

ROOT = Path(__file__).resolve().parents[1]


def test_migrated_schema_matches_runtime_metadata(backend: PostgresBackend) -> None:
    inspector = inspect(backend.engine)
    assert set(inspector.get_table_names()) == {
        table.name for table in METADATA.tables.values()
    } | {"whoop_alembic_version"}
    for table in METADATA.tables.values():
        actual = {col["name"]: col for col in inspector.get_columns(table.name)}
        assert set(actual) == set(table.c.keys())
        for column in table.c:
            assert actual[column.name]["nullable"] == column.nullable
            assert str(actual[column.name]["type"]) == str(column.type)
        assert inspector.get_pk_constraint(table.name)["constrained_columns"] == [
            col.name for col in table.primary_key
        ]


def test_migration_upgrade_is_repeatable_and_downgrade_is_reversible(
    backend: PostgresBackend,
) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    with backend.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        command.downgrade(config, "base")
    assert inspect(backend.engine).get_table_names() == ["whoop_alembic_version"]
    with backend.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    assert "whoop_tokens" in inspect(backend.engine).get_table_names()


def test_alembic_requires_opt_in_even_with_an_ambient_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:secret@db.invalid/whoop")
    monkeypatch.delenv("WHOOP_PIPELINE_USE_POSTGRES", raising=False)
    with pytest.raises(RuntimeError, match="explicitly enable"):
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


def test_postgres_migration_emits_private_schema_and_view_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WHOOP_PIPELINE_USE_POSTGRES", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:p%40ss%25@db.invalid/whoop")
    output = StringIO()
    command.upgrade(Config(str(ROOT / "alembic.ini"), output_buffer=output), "head", sql=True)
    sql = output.getvalue()
    assert "CREATE SCHEMA whoop" in sql
    assert "REVOKE ALL ON SCHEMA whoop FROM PUBLIC" in sql
    assert "CREATE VIEW whoop.daily_summary" in sql
    assert "FROM whoop.sleep" in sql
    assert "JOIN whoop.cycles" in sql
    for table in METADATA.tables.values():
        assert f"CREATE TABLE whoop.{table.name}" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "p%40ss" not in sql
    assert "p@ss" not in sql
