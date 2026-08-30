"""Compile the actual prod model with native Postgres connections blocked.

DuckDB executes the portable Postgres view/mart SQL on fixtures as a semantic cross-check;
this is still not a substitute for a real Postgres smoke test.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

import duckdb
import pytest
import sqlparse
from alembic import command
from alembic.config import Config
from dbt.cli.main import dbtRunner

from whoop_pipeline.storage.database import dbt_postgres_environment
from whoop_pipeline.storage.duckdb_loader import load_silver_frames
from whoop_pipeline.transform.flatten import (
    flatten_cycles,
    flatten_recovery,
    flatten_sleep,
    flatten_workouts,
)

ROOT = Path(__file__).resolve().parents[1]


def test_prod_model_compiles_offline_and_matches_fixture_features(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture_records: dict[str, list[dict[str, Any]]],
) -> None:
    url = "postgresql://fake:password-marker@db.invalid/offline_db"
    for key, value in dbt_postgres_environment(url).items():
        monkeypatch.setenv(key, value)
    target = tmp_path / "target"
    result = dbtRunner().invoke(
        [
            "compile",
            "--project-dir",
            str(ROOT / "dbt"),
            "--profiles-dir",
            str(ROOT / "dbt"),
            "--target",
            "prod",
            "--target-path",
            str(target),
            "--log-path",
            str(tmp_path / "logs"),
            "--no-introspect",
            "--no-populate-cache",
        ]
    )
    assert result.success, result.exception
    sql = (target / "compiled/whoop_features/models/marts/mart_daily_features.sql").read_text()
    assert '"offline_db"."whoop"."daily_summary"' in sql
    assert "ignore nulls" not in sql.lower()
    assert "dayofweek(" not in sql.lower()
    assert "extract(dow" in sql.lower()

    monkeypatch.setenv("WHOOP_PIPELINE_USE_POSTGRES", "true")
    monkeypatch.setenv("DATABASE_URL", url)
    output = StringIO()
    command.upgrade(Config(str(ROOT / "alembic.ini"), output_buffer=output), "head", sql=True)
    # Parse SQL statement boundaries: a semicolon inside a comment is not a terminator.
    view_sql = next(
        statement
        for statement in sqlparse.split(output.getvalue())
        if statement.lstrip().startswith("CREATE VIEW whoop.daily_summary")
    )

    database_path = tmp_path / "offline_db.duckdb"
    load_silver_frames(
        flatten_cycles(fixture_records["cycles"]),
        flatten_recovery(fixture_records["recovery"]),
        flatten_sleep(fixture_records["sleep"]),
        flatten_workouts(fixture_records["workouts"]),
        database_path=database_path,
    )
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("CREATE SCHEMA whoop")
        for table in ("cycles", "recovery", "sleep", "workouts"):
            connection.execute(f"CREATE VIEW whoop.{table} AS SELECT * FROM main.{table}")
        connection.execute(view_sql)
        postgres_daily = connection.execute(
            "SELECT * FROM whoop.daily_summary ORDER BY cycle_id"
        ).fetchall()
        duckdb_daily = connection.execute(
            "SELECT * FROM main.daily_summary ORDER BY cycle_id"
        ).fetchall()
        assert postgres_daily == duckdb_daily
        connection.execute(f"CREATE TABLE compiled_mart AS {sql}")
        features = connection.execute(
            "SELECT cycle_id, day_of_week, prior_day_strain, cycle_strain_7d_avg, "
            "days_since_last_low_strain_day FROM compiled_mart ORDER BY start_at"
        ).fetchall()
        assert len(features) == 2
        assert features[0][2] is None
        assert features[1][2] == fixture_records["cycles"][0]["score"]["strain"]
        assert all(0 <= row[1] <= 6 for row in features)
