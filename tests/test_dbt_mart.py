"""Builds a throwaway DuckDB from Phase 1 fixtures and runs the real dbt project against it.

Never touches ``data/processed/whoop.db``; ``WHOOP_DUCKDB_PATH`` points dbt at a tmp_path file
instead, exercising the exact same profile the Dagster asset and a human would use.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import duckdb

from whoop_pipeline.storage.duckdb_loader import load_silver_frames
from whoop_pipeline.transform.flatten import (
    flatten_cycles,
    flatten_recovery,
    flatten_sleep,
    flatten_workouts,
)

DBT_PROJECT_DIR = Path(__file__).resolve().parent.parent / "dbt"

EXPECTED_MART_COLUMNS = {
    "cycle_id",
    "user_id",
    "start_at",
    "day_of_week",
    "cycle_strain",
    "recovery_score",
    "hrv_rmssd_milli",
    "sleep_debt_need_hours",
    "recovery_score_7d_avg",
    "cycle_strain_7d_avg",
    "hrv_rmssd_milli_7d_avg",
    "prior_day_strain",
    "sleep_debt_7d_avg_hours",
    "sleep_debt_trend_hours",
    "days_since_last_low_strain_day",
}


def test_dbt_build_produces_mart_with_expected_columns_and_rows(
    tmp_path: Path,
    monkeypatch: Any,
    fixture_records: dict[str, list[dict[str, Any]]],
) -> None:
    database_path = tmp_path / "whoop.db"
    load_silver_frames(
        flatten_cycles(fixture_records["cycles"]),
        flatten_recovery(fixture_records["recovery"]),
        flatten_sleep(fixture_records["sleep"]),
        flatten_workouts(fixture_records["workouts"]),
        database_path=database_path,
    )

    monkeypatch.setenv("WHOOP_DUCKDB_PATH", str(database_path))
    dbt_executable = shutil.which("dbt")
    assert dbt_executable is not None, "dbt console script not found on PATH"
    process = subprocess.run(
        [
            dbt_executable,
            "build",
            "--project-dir",
            str(DBT_PROJECT_DIR),
            "--profiles-dir",
            str(DBT_PROJECT_DIR),
            "--target-path",
            str(tmp_path / "dbt_target"),
            "--log-path",
            str(tmp_path / "dbt_logs"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0, process.stdout + process.stderr

    with duckdb.connect(str(database_path), read_only=True) as connection:
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'mart_daily_features'"
            ).fetchall()
        }
        row_count = connection.execute("SELECT COUNT(*) FROM mart_daily_features").fetchone()[0]

    assert columns >= EXPECTED_MART_COLUMNS
    assert row_count == 2
