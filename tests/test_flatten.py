from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from whoop_pipeline.transform.flatten import (
    flatten_cycles,
    flatten_recovery,
    flatten_sleep,
    flatten_workouts,
    join_daily,
)


def test_flatten_cycles_handles_pending_score(
    fixture_records: dict[str, list[dict[str, Any]]],
) -> None:
    frame = flatten_cycles(fixture_records["cycles"])

    assert list(frame["cycle_id"]) == [93845, 93846]
    assert frame.loc[0, "strain"] == pytest.approx(5.2951527)
    assert pd.isna(frame.loc[1, "strain"])
    assert pd.isna(frame.loc[1, "end_at"])


def test_flatten_recovery_includes_optional_hardware_fields_and_nulls(
    fixture_records: dict[str, list[dict[str, Any]]],
) -> None:
    frame = flatten_recovery(fixture_records["recovery"])

    assert frame.loc[0, "spo2_percentage"] == pytest.approx(95.6875)
    assert frame.loc[0, "skin_temp_celsius"] == pytest.approx(33.7)
    assert frame.loc[0, "hrv_rmssd_milli"] == pytest.approx(31.813562)
    assert pd.isna(frame.loc[1, "recovery_score"])


def test_flatten_sleep_converts_milliseconds_to_hours_and_handles_pending(
    fixture_records: dict[str, list[dict[str, Any]]],
) -> None:
    frame = flatten_sleep(fixture_records["sleep"])

    assert frame.loc[0, "total_in_bed_hours"] == pytest.approx(30272735 / 1000 / 3600)
    assert frame.loc[0, "recent_nap_need_hours"] == pytest.approx(-12312 / 1000 / 3600)
    assert bool(frame.loc[1, "is_nap"])
    assert pd.isna(frame.loc[1, "sleep_efficiency_percentage"])


def test_flatten_workouts_uses_sport_name_and_converts_zones(
    fixture_records: dict[str, list[dict[str, Any]]],
) -> None:
    frame = flatten_workouts(fixture_records["workouts"])

    assert "sport_id" not in frame.columns
    assert frame.loc[0, "sport_name"] == "running"
    assert frame.loc[0, "zone_2_hours"] == pytest.approx(0.25)
    assert pd.isna(frame.loc[1, "strain"])


def test_join_daily_excludes_naps_and_assigns_workouts_by_time(
    fixture_records: dict[str, list[dict[str, Any]]],
) -> None:
    daily = join_daily(
        flatten_cycles(fixture_records["cycles"]),
        flatten_recovery(fixture_records["recovery"]),
        flatten_sleep(fixture_records["sleep"]),
        flatten_workouts(fixture_records["workouts"]),
    )

    assert list(daily["cycle_id"]) == [93845, 93846]
    assert daily.loc[0, "recovery_score"] == 44
    assert daily.loc[0, "sleep_id"] == "ecfc6a15-4661-442f-a9a4-f160dd7afae8"
    assert daily.loc[0, "workout_count"] == 1
    assert daily.loc[0, "workout_total_duration_hours"] == pytest.approx(1.0)
    assert pd.isna(daily.loc[1, "sleep_id"])
    assert daily.loc[1, "workout_count"] == 1


def test_flatten_functions_return_typed_empty_frames() -> None:
    frames = [flatten_cycles([]), flatten_recovery([]), flatten_sleep([]), flatten_workouts([])]

    assert all(frame.empty for frame in frames)
    assert str(frames[0]["cycle_id"].dtype) == "Int64"
    assert str(frames[1]["score_state"].dtype) == "string"
