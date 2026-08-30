from __future__ import annotations

from typing import Any

import pandera.pandas as pa
import pytest

from whoop_pipeline.transform.flatten import (
    flatten_cycles,
    flatten_recovery,
    flatten_sleep,
    flatten_workouts,
)
from whoop_pipeline.validation.schemas import SILVER_SCHEMAS, validate_silver_frames


@pytest.fixture
def silver_frames(fixture_records: dict[str, list[dict[str, Any]]]):
    return {
        "cycles": flatten_cycles(fixture_records["cycles"]),
        "recovery": flatten_recovery(fixture_records["recovery"]),
        "sleep": flatten_sleep(fixture_records["sleep"]),
        "workouts": flatten_workouts(fixture_records["workouts"]),
    }


def test_all_silver_schemas_accept_verified_fixtures(silver_frames) -> None:
    validate_silver_frames(silver_frames)


@pytest.mark.parametrize(
    ("frame_name", "column", "bad_value"),
    [
        ("cycles", "strain", -0.1),
        ("recovery", "recovery_score", 101),
        ("sleep", "total_in_bed_hours", -1.0),
        ("workouts", "percent_recorded", 101.0),
    ],
)
def test_each_schema_rejects_deliberately_invalid_data(
    silver_frames, frame_name: str, column: str, bad_value: float
) -> None:
    invalid = silver_frames[frame_name].copy()
    invalid.loc[0, column] = bad_value

    with pytest.raises(pa.errors.SchemaErrors):
        SILVER_SCHEMAS[frame_name].validate(invalid, lazy=True)


def test_validation_rejects_missing_frame(silver_frames) -> None:
    del silver_frames["sleep"]
    with pytest.raises(ValueError, match="sleep"):
        validate_silver_frames(silver_frames)
