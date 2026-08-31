"""Cross-check the seeded generator's real-SQL harness against a complete dbt build."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from whoop_pipeline.modeling.synthetic import synthetic_daily, synthetic_history


def test_synthetic_features_match_real_dbt_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "synthetic.db"
    daily = synthetic_daily()
    with duckdb.connect(str(database)) as connection:
        connection.register("synthetic_input", daily)
        connection.execute("CREATE TABLE daily_summary AS SELECT * FROM synthetic_input")
    monkeypatch.setenv("WHOOP_DUCKDB_PATH", str(database))
    project = Path(__file__).resolve().parents[1] / "dbt"
    executable = shutil.which("dbt")
    assert executable is not None
    # Run in a child process so dbt's connection pool cannot retain a handle in pytest.
    result = subprocess.run(
        [
            executable,
            "build",
            "--select",
            "mart_daily_features",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project),
            "--target",
            "dev",
            "--target-path",
            str(tmp_path / "target"),
            "--log-path",
            str(tmp_path / "logs"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    with duckdb.connect(str(database), read_only=True) as connection:
        connection.execute("SET TimeZone='America/New_York'")
        actual = connection.execute("SELECT * FROM mart_daily_features ORDER BY start_at").fetchdf()
    expected = synthetic_history()[actual.columns]
    actual["start_at"] = pd.to_datetime(actual["start_at"], utc=True)
    assert actual["day_of_week"].tolist() == ((daily["start_at"].dt.dayofweek + 1) % 7).tolist()
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False)
    assert np.isfinite(actual["recovery_score_7d_avg"]).all()
