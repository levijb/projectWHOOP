"""Pandera contracts enforced immediately before silver data reaches DuckDB."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd
import pandera.pandas as pa
from pandera import Check

UTC_DATETIME = pd.DatetimeTZDtype(unit="ns", tz="UTC")
SCORE_STATE = Check.isin(["SCORED", "PENDING_SCORE", "UNSCORABLE"])
NON_NEGATIVE = Check.greater_than_or_equal_to(0)
PERCENTAGE = Check.in_range(0, 100)

CYCLES_SCHEMA = pa.DataFrameSchema(
    {
        "cycle_id": pa.Column("Int64", nullable=False, unique=True),
        "user_id": pa.Column("Int64", nullable=False),
        "created_at": pa.Column(UTC_DATETIME, nullable=False),
        "updated_at": pa.Column(UTC_DATETIME, nullable=False),
        "start_at": pa.Column(UTC_DATETIME, nullable=False),
        "end_at": pa.Column(UTC_DATETIME, nullable=True),
        "timezone_offset": pa.Column("string", nullable=False),
        "score_state": pa.Column("string", SCORE_STATE, nullable=False),
        "strain": pa.Column("Float64", NON_NEGATIVE, nullable=True),
        "kilojoule": pa.Column("Float64", NON_NEGATIVE, nullable=True),
        "average_heart_rate": pa.Column("Int64", NON_NEGATIVE, nullable=True),
        "max_heart_rate": pa.Column("Int64", NON_NEGATIVE, nullable=True),
    },
    strict=True,
)

RECOVERY_SCHEMA = pa.DataFrameSchema(
    {
        "cycle_id": pa.Column("Int64", nullable=False, unique=True),
        "sleep_id": pa.Column("string", nullable=False),
        "user_id": pa.Column("Int64", nullable=False),
        "created_at": pa.Column(UTC_DATETIME, nullable=False),
        "updated_at": pa.Column(UTC_DATETIME, nullable=False),
        "score_state": pa.Column("string", SCORE_STATE, nullable=False),
        "user_calibrating": pa.Column("boolean", nullable=True),
        "recovery_score": pa.Column("Int64", PERCENTAGE, nullable=True),
        "resting_heart_rate": pa.Column("Int64", NON_NEGATIVE, nullable=True),
        "hrv_rmssd_milli": pa.Column("Float64", NON_NEGATIVE, nullable=True),
        "spo2_percentage": pa.Column("Float64", PERCENTAGE, nullable=True),
        "skin_temp_celsius": pa.Column("Float64", Check.in_range(0, 60), nullable=True),
    },
    strict=True,
)

SLEEP_DURATION_COLUMNS = (
    "total_in_bed_hours",
    "total_awake_hours",
    "total_no_data_hours",
    "total_light_sleep_hours",
    "total_slow_wave_sleep_hours",
    "total_rem_sleep_hours",
    "baseline_sleep_need_hours",
    "sleep_debt_need_hours",
    "recent_strain_need_hours",
)

SLEEP_SCHEMA_COLUMNS: dict[str, pa.Column] = {
    "sleep_id": pa.Column("string", nullable=False, unique=True),
    "cycle_id": pa.Column("Int64", nullable=False),
    "user_id": pa.Column("Int64", nullable=False),
    "created_at": pa.Column(UTC_DATETIME, nullable=False),
    "updated_at": pa.Column(UTC_DATETIME, nullable=False),
    "start_at": pa.Column(UTC_DATETIME, nullable=False),
    "end_at": pa.Column(UTC_DATETIME, nullable=False),
    "timezone_offset": pa.Column("string", nullable=False),
    "is_nap": pa.Column("boolean", nullable=False),
    "score_state": pa.Column("string", SCORE_STATE, nullable=False),
    **{
        column: pa.Column("Float64", NON_NEGATIVE, nullable=True)
        for column in SLEEP_DURATION_COLUMNS
    },
    # Recent naps reduce current need, so this one duration can legitimately be negative.
    "recent_nap_need_hours": pa.Column("Float64", nullable=True),
    "sleep_cycle_count": pa.Column("Int64", NON_NEGATIVE, nullable=True),
    "disturbance_count": pa.Column("Int64", NON_NEGATIVE, nullable=True),
    "respiratory_rate": pa.Column("Float64", NON_NEGATIVE, nullable=True),
    "sleep_performance_percentage": pa.Column("Float64", PERCENTAGE, nullable=True),
    "sleep_consistency_percentage": pa.Column("Float64", PERCENTAGE, nullable=True),
    "sleep_efficiency_percentage": pa.Column("Float64", PERCENTAGE, nullable=True),
}
SLEEP_SCHEMA = pa.DataFrameSchema(SLEEP_SCHEMA_COLUMNS, strict=True)

WORKOUT_SCHEMA_COLUMNS: dict[str, pa.Column] = {
    "workout_id": pa.Column("string", nullable=False, unique=True),
    "user_id": pa.Column("Int64", nullable=False),
    "created_at": pa.Column(UTC_DATETIME, nullable=False),
    "updated_at": pa.Column(UTC_DATETIME, nullable=False),
    "start_at": pa.Column(UTC_DATETIME, nullable=False),
    "end_at": pa.Column(UTC_DATETIME, nullable=False),
    "timezone_offset": pa.Column("string", nullable=False),
    "sport_name": pa.Column("string", nullable=False),
    "score_state": pa.Column("string", SCORE_STATE, nullable=False),
    "strain": pa.Column("Float64", NON_NEGATIVE, nullable=True),
    "average_heart_rate": pa.Column("Int64", NON_NEGATIVE, nullable=True),
    "max_heart_rate": pa.Column("Int64", NON_NEGATIVE, nullable=True),
    "kilojoule": pa.Column("Float64", NON_NEGATIVE, nullable=True),
    "percent_recorded": pa.Column("Float64", PERCENTAGE, nullable=True),
    "distance_meter": pa.Column("Float64", NON_NEGATIVE, nullable=True),
    "altitude_gain_meter": pa.Column("Float64", nullable=True),
    "altitude_change_meter": pa.Column("Float64", nullable=True),
    **{
        f"zone_{zone}_hours": pa.Column("Float64", NON_NEGATIVE, nullable=True) for zone in range(6)
    },
}
WORKOUT_SCHEMA = pa.DataFrameSchema(WORKOUT_SCHEMA_COLUMNS, strict=True)

SILVER_SCHEMAS: Mapping[str, pa.DataFrameSchema] = {
    "cycles": CYCLES_SCHEMA,
    "recovery": RECOVERY_SCHEMA,
    "sleep": SLEEP_SCHEMA,
    "workouts": WORKOUT_SCHEMA,
}


def validate_silver_frames(frames: Mapping[str, pd.DataFrame]) -> None:
    """Validate every silver frame and fail before opening a gold transaction."""
    missing = [name for name in SILVER_SCHEMAS if name not in frames]
    if missing:
        raise ValueError(f"Missing silver DataFrame(s): {', '.join(missing)}")
    for name, schema in SILVER_SCHEMAS.items():
        schema.validate(frames[name], lazy=True)
