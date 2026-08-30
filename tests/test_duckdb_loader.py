from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pandera.pandas as pa
import pytest

from whoop_pipeline.storage.duckdb_loader import load_silver_frames
from whoop_pipeline.transform.flatten import (
    flatten_cycles,
    flatten_recovery,
    flatten_sleep,
    flatten_workouts,
)


def _frames(records: dict[str, list[dict[str, Any]]]):
    return (
        flatten_cycles(records["cycles"]),
        flatten_recovery(records["recovery"]),
        flatten_sleep(records["sleep"]),
        flatten_workouts(records["workouts"]),
    )


def test_loader_is_idempotent_and_daily_view_matches_join_policy(
    tmp_path: Path, fixture_records: dict[str, list[dict[str, Any]]]
) -> None:
    database_path = tmp_path / "processed" / "whoop.db"
    frames = _frames(fixture_records)

    load_silver_frames(*frames, database_path=database_path)
    load_silver_frames(*frames, database_path=database_path)

    with duckdb.connect(str(database_path), read_only=True) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("cycles", "recovery", "sleep", "workouts")
        }
        daily = connection.execute(
            "SELECT cycle_id, sleep_id, workout_count FROM daily_summary ORDER BY cycle_id"
        ).fetchall()
    assert counts == {"cycles": 2, "recovery": 2, "sleep": 2, "workouts": 2}
    assert daily[0] == (93845, "ecfc6a15-4661-442f-a9a4-f160dd7afae8", 1)
    assert daily[1] == (93846, None, 1)


def test_loader_updates_existing_id_instead_of_duplicating(
    tmp_path: Path, fixture_records: dict[str, list[dict[str, Any]]]
) -> None:
    database_path = tmp_path / "whoop.db"
    frames = list(_frames(fixture_records))
    load_silver_frames(*frames, database_path=database_path)
    frames[0].loc[0, "strain"] = 6.5
    load_silver_frames(*frames, database_path=database_path)

    with duckdb.connect(str(database_path), read_only=True) as connection:
        row = connection.execute(
            "SELECT COUNT(*), MAX(strain) FROM cycles WHERE cycle_id = 93845"
        ).fetchone()
    assert row == (1, 6.5)


def test_loader_validates_before_creating_database(
    tmp_path: Path, fixture_records: dict[str, list[dict[str, Any]]]
) -> None:
    database_path = tmp_path / "invalid.db"
    frames = list(_frames(fixture_records))
    frames[1].loc[0, "recovery_score"] = 999

    with pytest.raises(pa.errors.SchemaErrors):
        load_silver_frames(*frames, database_path=database_path)
    assert not database_path.exists()
