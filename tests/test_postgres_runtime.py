"""Optional real-PostgreSQL-engine regression using PGlite's in-memory WASM runtime.

Install @electric-sql/pglite in a temporary directory and set PROJECTWHOOP_PGLITE_DIR to that
directory to enable these tests. No server, database URL, or production dependency is needed.
The standard Python-only suite skips them when the optional runtime is unavailable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from copy import deepcopy
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import duckdb
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.dialects import postgresql

from whoop_pipeline.storage.duckdb_loader import load_silver_frames
from whoop_pipeline.storage.postgres_backend import _sql_records
from whoop_pipeline.storage.postgres_models import GOLD_TABLES
from whoop_pipeline.transform.flatten import (
    flatten_cycles,
    flatten_recovery,
    flatten_sleep,
    flatten_workouts,
)

ROOT = Path(__file__).resolve().parents[1]
PGLITE_RUNNER = """
const { readFileSync } = await import('node:fs');
const { pathToFileURL } = await import('node:url');
const { Socket } = await import('node:net');
// The WASM engine uses local package files and memory. Forbid network connections.
Socket.prototype.connect = function () { throw new Error('Network forbidden'); };
globalThis.fetch = async function () { throw new Error('Network forbidden'); };
const { PGlite } = await import(pathToFileURL(process.argv[1]).href);
const input = JSON.parse(readFileSync(0, 'utf8'));
const db = await PGlite.create({ parsers: { 1184: value => value, 1700: Number } });
try {
    await db.exec(input.ddl);
    await db.exec(input.inserts);
    const result = await db.query('SELECT * FROM whoop.daily_summary ORDER BY cycle_id');
    process.stdout.write(JSON.stringify(result));
} catch (error) {
    process.stderr.write(JSON.stringify({code: error.code, message: error.message}));
    process.exitCode = 1;
} finally {
    await db.close();
}
"""


@pytest.mark.parametrize("scenario", ["baseline", "overlapping_cycles", "boundaries_and_gap"])
def test_daily_summary_on_embedded_postgres(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture_records: dict[str, list[dict[str, Any]]],
    scenario: str,
) -> None:
    module_dir = os.environ.get("PROJECTWHOOP_PGLITE_DIR")
    if not module_dir:
        pytest.skip("Optional embedded Postgres: set PROJECTWHOOP_PGLITE_DIR")
    module = Path(module_dir) / "node_modules/@electric-sql/pglite/dist/index.js"
    assert module.is_file(), "PROJECTWHOOP_PGLITE_DIR must contain an installed PGlite package"
    node = shutil.which("node")
    assert node is not None, "Node is required for the optional embedded PostgreSQL check"

    records = deepcopy(fixture_records)
    expected_counts = {93845: 1, 93846: 1}
    if scenario == "overlapping_cycles":
        records["cycles"][0].pop("end")
        empty_cycle = deepcopy(records["cycles"][1])
        empty_cycle.update(
            id=93847, start="2022-04-26T02:25:44.774Z", end="2022-04-27T02:25:44.774Z"
        )
        records["cycles"].append(empty_cycle)
        expected_counts[93847] = 0
    elif scenario == "boundaries_and_gap":
        for number, start, end in (
            ("boundary", records["cycles"][1]["start"], "2022-04-25T03:25:44.774Z"),
            ("gap", records["cycles"][0]["end"], "2022-04-24T11:25:44.774Z"),
            ("before", "2022-04-23T01:00:00.000Z", "2022-04-23T02:00:00.000Z"),
        ):
            workout = deepcopy(records["workouts"][0])
            workout.update(id=f"fixture-{number}", start=start, end=end)
            records["workouts"].append(workout)
        expected_counts[93846] = 2

    frames = (
        flatten_cycles(records["cycles"]),
        flatten_recovery(records["recovery"]),
        flatten_sleep(records["sleep"]),
        flatten_workouts(records["workouts"]),
    )
    assert "cycle_id" not in frames[3].columns

    # SQL-only Alembic rendering; never consume any ambient connection URL.
    monkeypatch.setenv("WHOOP_PIPELINE_USE_POSTGRES", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://offline:fake@db.invalid/offline")
    output = StringIO()
    command.upgrade(Config(str(ROOT / "alembic.ini"), output_buffer=output), "head", sql=True)
    inserts = []
    for (table, _key), frame in zip(GOLD_TABLES.values(), frames, strict=True):
        inserts.extend(
            str(
                table.insert()
                .values(row)
                .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
            )
            + ";"
            for row in _sql_records(frame)
        )
    child_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("WHOOP_", "PG", "DBT_ENV_SECRET_")) and key != "DATABASE_URL"
    }
    process = subprocess.run(
        [node, "--input-type=module", "-e", PGLITE_RUNNER, str(module)],
        input=json.dumps({"ddl": output.getvalue(), "inserts": "\n".join(inserts)}),
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
        cwd=tmp_path,
        env=child_env,
    )
    assert process.returncode == 0, process.stderr
    actual = json.loads(process.stdout)

    database_path = tmp_path / "expected.duckdb"
    load_silver_frames(*frames, database_path=database_path)
    with duckdb.connect(str(database_path), read_only=True) as connection:
        cursor = connection.execute("SELECT * FROM daily_summary ORDER BY cycle_id")
        columns = [column[0] for column in cursor.description]
        expected = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    assert [field["name"] for field in actual["fields"]] == columns
    assert len(columns) == len(set(columns))
    assert len(actual["rows"]) == len(expected)
    assert {row["cycle_id"]: row["workout_count"] for row in actual["rows"]} == expected_counts
    for actual_row, expected_row in zip(actual["rows"], expected, strict=True):
        for name, expected_value in expected_row.items():
            value = actual_row[name]
            if isinstance(expected_value, datetime):
                assert datetime.fromisoformat(value).astimezone(UTC) == expected_value
            elif isinstance(expected_value, float):
                assert value == pytest.approx(expected_value)
            else:
                assert value == expected_value
