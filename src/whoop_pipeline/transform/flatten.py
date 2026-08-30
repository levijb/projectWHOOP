"""Pure WHOOP v2 record flattening and daily feature assembly.

API duration fields ending in ``_milli`` are converted to decimal hours for sleep and workout
durations. Recovery's ``hrv_rmssd_milli`` is intentionally retained in milliseconds because
it is a physiological measurement, not a duration to aggregate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pandas as pd

Record = Mapping[str, Any]


def _score(record: Record) -> Record:
    score = record.get("score")
    return score if record.get("score_state") == "SCORED" and isinstance(score, Mapping) else {}


def _nested(parent: Record, key: str) -> Record:
    value = parent.get(key)
    return value if isinstance(value, Mapping) else {}


def _milliseconds_to_hours(value: Any) -> float | None:
    if value is None:
        return None
    return float(value) / 1000.0 / 3600.0


def _typed_frame(
    rows: list[dict[str, Any]],
    columns: tuple[str, ...],
    *,
    timestamps: Sequence[str],
    integers: Sequence[str] = (),
    floats: Sequence[str] = (),
    booleans: Sequence[str] = (),
    strings: Sequence[str] = (),
) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(rows, columns=columns)
    for column in timestamps:
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce").astype(
            "datetime64[ns, UTC]"
        )
    for column in integers:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    for column in floats:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Float64")
    for column in booleans:
        frame[column] = frame[column].astype("boolean")
    for column in strings:
        frame[column] = frame[column].astype("string")
    return frame


def flatten_cycles(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Flatten cycle records, leaving score columns null for unscored cycles."""
    rows = []
    for record in records:
        score = _score(record)
        rows.append(
            {
                "cycle_id": record.get("id"),
                "user_id": record.get("user_id"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "start_at": record.get("start"),
                "end_at": record.get("end"),
                "timezone_offset": record.get("timezone_offset"),
                "score_state": record.get("score_state"),
                "strain": score.get("strain"),
                "kilojoule": score.get("kilojoule"),
                "average_heart_rate": score.get("average_heart_rate"),
                "max_heart_rate": score.get("max_heart_rate"),
            }
        )
    columns = (
        "cycle_id",
        "user_id",
        "created_at",
        "updated_at",
        "start_at",
        "end_at",
        "timezone_offset",
        "score_state",
        "strain",
        "kilojoule",
        "average_heart_rate",
        "max_heart_rate",
    )
    return _typed_frame(
        rows,
        columns,
        timestamps=("created_at", "updated_at", "start_at", "end_at"),
        integers=("cycle_id", "user_id", "average_heart_rate", "max_heart_rate"),
        floats=("strain", "kilojoule"),
        strings=("timezone_offset", "score_state"),
    )


def flatten_recovery(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Flatten recovery, including optional WHOOP 4.0 SpO2 and skin temperature."""
    rows = []
    for record in records:
        score = _score(record)
        rows.append(
            {
                "cycle_id": record.get("cycle_id"),
                "sleep_id": record.get("sleep_id"),
                "user_id": record.get("user_id"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "score_state": record.get("score_state"),
                "user_calibrating": score.get("user_calibrating"),
                "recovery_score": score.get("recovery_score"),
                "resting_heart_rate": score.get("resting_heart_rate"),
                "hrv_rmssd_milli": score.get("hrv_rmssd_milli"),
                "spo2_percentage": score.get("spo2_percentage"),
                "skin_temp_celsius": score.get("skin_temp_celsius"),
            }
        )
    columns = (
        "cycle_id",
        "sleep_id",
        "user_id",
        "created_at",
        "updated_at",
        "score_state",
        "user_calibrating",
        "recovery_score",
        "resting_heart_rate",
        "hrv_rmssd_milli",
        "spo2_percentage",
        "skin_temp_celsius",
    )
    return _typed_frame(
        rows,
        columns,
        timestamps=("created_at", "updated_at"),
        integers=("cycle_id", "user_id", "recovery_score", "resting_heart_rate"),
        floats=("hrv_rmssd_milli", "spo2_percentage", "skin_temp_celsius"),
        booleans=("user_calibrating",),
        strings=("sleep_id", "score_state"),
    )


def flatten_sleep(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Flatten sleep and convert every stage/need duration from milliseconds to hours."""
    rows = []
    for record in records:
        score = _score(record)
        stages = _nested(score, "stage_summary")
        needed = _nested(score, "sleep_needed")
        rows.append(
            {
                "sleep_id": record.get("id"),
                "cycle_id": record.get("cycle_id"),
                "user_id": record.get("user_id"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "start_at": record.get("start"),
                "end_at": record.get("end"),
                "timezone_offset": record.get("timezone_offset"),
                "is_nap": record.get("nap"),
                "score_state": record.get("score_state"),
                "total_in_bed_hours": _milliseconds_to_hours(stages.get("total_in_bed_time_milli")),
                "total_awake_hours": _milliseconds_to_hours(stages.get("total_awake_time_milli")),
                "total_no_data_hours": _milliseconds_to_hours(
                    stages.get("total_no_data_time_milli")
                ),
                "total_light_sleep_hours": _milliseconds_to_hours(
                    stages.get("total_light_sleep_time_milli")
                ),
                "total_slow_wave_sleep_hours": _milliseconds_to_hours(
                    stages.get("total_slow_wave_sleep_time_milli")
                ),
                "total_rem_sleep_hours": _milliseconds_to_hours(
                    stages.get("total_rem_sleep_time_milli")
                ),
                "sleep_cycle_count": stages.get("sleep_cycle_count"),
                "disturbance_count": stages.get("disturbance_count"),
                "baseline_sleep_need_hours": _milliseconds_to_hours(needed.get("baseline_milli")),
                "sleep_debt_need_hours": _milliseconds_to_hours(
                    needed.get("need_from_sleep_debt_milli")
                ),
                "recent_strain_need_hours": _milliseconds_to_hours(
                    needed.get("need_from_recent_strain_milli")
                ),
                "recent_nap_need_hours": _milliseconds_to_hours(
                    needed.get("need_from_recent_nap_milli")
                ),
                "respiratory_rate": score.get("respiratory_rate"),
                "sleep_performance_percentage": score.get("sleep_performance_percentage"),
                "sleep_consistency_percentage": score.get("sleep_consistency_percentage"),
                "sleep_efficiency_percentage": score.get("sleep_efficiency_percentage"),
            }
        )
    columns = (
        tuple(rows[0])
        if rows
        else (
            "sleep_id",
            "cycle_id",
            "user_id",
            "created_at",
            "updated_at",
            "start_at",
            "end_at",
            "timezone_offset",
            "is_nap",
            "score_state",
            "total_in_bed_hours",
            "total_awake_hours",
            "total_no_data_hours",
            "total_light_sleep_hours",
            "total_slow_wave_sleep_hours",
            "total_rem_sleep_hours",
            "sleep_cycle_count",
            "disturbance_count",
            "baseline_sleep_need_hours",
            "sleep_debt_need_hours",
            "recent_strain_need_hours",
            "recent_nap_need_hours",
            "respiratory_rate",
            "sleep_performance_percentage",
            "sleep_consistency_percentage",
            "sleep_efficiency_percentage",
        )
    )
    float_columns = (
        "total_in_bed_hours",
        "total_awake_hours",
        "total_no_data_hours",
        "total_light_sleep_hours",
        "total_slow_wave_sleep_hours",
        "total_rem_sleep_hours",
        "baseline_sleep_need_hours",
        "sleep_debt_need_hours",
        "recent_strain_need_hours",
        "recent_nap_need_hours",
        "respiratory_rate",
        "sleep_performance_percentage",
        "sleep_consistency_percentage",
        "sleep_efficiency_percentage",
    )
    return _typed_frame(
        rows,
        columns,
        timestamps=("created_at", "updated_at", "start_at", "end_at"),
        integers=("cycle_id", "user_id", "sleep_cycle_count", "disturbance_count"),
        floats=float_columns,
        booleans=("is_nap",),
        strings=("sleep_id", "timezone_offset", "score_state"),
    )


def flatten_workouts(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Flatten workouts using ``sport_name`` and convert zone milliseconds to hours."""
    rows = []
    for record in records:
        score = _score(record)
        zones = _nested(score, "zone_durations")
        row: dict[str, Any] = {
            "workout_id": record.get("id"),
            "user_id": record.get("user_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "start_at": record.get("start"),
            "end_at": record.get("end"),
            "timezone_offset": record.get("timezone_offset"),
            "sport_name": record.get("sport_name"),
            "score_state": record.get("score_state"),
            "strain": score.get("strain"),
            "average_heart_rate": score.get("average_heart_rate"),
            "max_heart_rate": score.get("max_heart_rate"),
            "kilojoule": score.get("kilojoule"),
            "percent_recorded": score.get("percent_recorded"),
            "distance_meter": score.get("distance_meter"),
            "altitude_gain_meter": score.get("altitude_gain_meter"),
            "altitude_change_meter": score.get("altitude_change_meter"),
        }
        for zone in range(6):
            row[f"zone_{zone}_hours"] = _milliseconds_to_hours(
                zones.get(f"zone_{['zero', 'one', 'two', 'three', 'four', 'five'][zone]}_milli")
            )
        rows.append(row)
    columns = (
        tuple(rows[0])
        if rows
        else (
            "workout_id",
            "user_id",
            "created_at",
            "updated_at",
            "start_at",
            "end_at",
            "timezone_offset",
            "sport_name",
            "score_state",
            "strain",
            "average_heart_rate",
            "max_heart_rate",
            "kilojoule",
            "percent_recorded",
            "distance_meter",
            "altitude_gain_meter",
            "altitude_change_meter",
            "zone_0_hours",
            "zone_1_hours",
            "zone_2_hours",
            "zone_3_hours",
            "zone_4_hours",
            "zone_5_hours",
        )
    )
    float_columns = (
        "strain",
        "kilojoule",
        "percent_recorded",
        "distance_meter",
        "altitude_gain_meter",
        "altitude_change_meter",
        "zone_0_hours",
        "zone_1_hours",
        "zone_2_hours",
        "zone_3_hours",
        "zone_4_hours",
        "zone_5_hours",
    )
    return _typed_frame(
        rows,
        columns,
        timestamps=("created_at", "updated_at", "start_at", "end_at"),
        integers=("user_id", "average_heart_rate", "max_heart_rate"),
        floats=float_columns,
        strings=("workout_id", "timezone_offset", "sport_name", "score_state"),
    )


def join_daily(
    cycles_df: pd.DataFrame,
    recovery_df: pd.DataFrame,
    sleep_df: pd.DataFrame,
    workouts_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build one row per cycle with recovery, main sleep, and workout aggregates.

    Naps are intentionally excluded from daily sleep metrics. Since current v2 workout records
    have no cycle ID, workouts are assigned to the cycle interval containing their start time.
    """
    cycles = cycles_df.copy().sort_values("start_at").drop_duplicates("cycle_id", keep="last")
    cycles = cycles.rename(
        columns={
            "strain": "cycle_strain",
            "kilojoule": "cycle_kilojoule",
            "average_heart_rate": "cycle_average_heart_rate",
            "max_heart_rate": "cycle_max_heart_rate",
            "score_state": "cycle_score_state",
        }
    )

    recovery = recovery_df.copy().sort_values("updated_at").drop_duplicates("cycle_id", keep="last")
    recovery_columns = [
        "cycle_id",
        "score_state",
        "user_calibrating",
        "recovery_score",
        "resting_heart_rate",
        "hrv_rmssd_milli",
        "spo2_percentage",
        "skin_temp_celsius",
    ]
    recovery = recovery[recovery_columns].rename(columns={"score_state": "recovery_score_state"})

    main_sleep = sleep_df.loc[~sleep_df["is_nap"].fillna(False)].copy()
    main_sleep = main_sleep.sort_values("updated_at").drop_duplicates("cycle_id", keep="last")
    sleep_columns = [
        "cycle_id",
        "sleep_id",
        "score_state",
        "total_in_bed_hours",
        "total_awake_hours",
        "total_no_data_hours",
        "total_light_sleep_hours",
        "total_slow_wave_sleep_hours",
        "total_rem_sleep_hours",
        "sleep_cycle_count",
        "disturbance_count",
        "baseline_sleep_need_hours",
        "sleep_debt_need_hours",
        "recent_strain_need_hours",
        "recent_nap_need_hours",
        "respiratory_rate",
        "sleep_performance_percentage",
        "sleep_consistency_percentage",
        "sleep_efficiency_percentage",
    ]
    main_sleep = main_sleep[sleep_columns].rename(columns={"score_state": "sleep_score_state"})

    workout_aggregates = _aggregate_workouts_by_cycle(cycles, workouts_df)
    daily = cycles.merge(recovery, on="cycle_id", how="left", validate="one_to_one")
    daily = daily.merge(main_sleep, on="cycle_id", how="left", validate="one_to_one")
    daily = daily.merge(workout_aggregates, on="cycle_id", how="left", validate="one_to_one")
    daily["workout_count"] = daily["workout_count"].fillna(0).astype("Int64")
    for column in (
        "workout_total_strain",
        "workout_max_strain",
        "workout_total_kilojoule",
        "workout_total_duration_hours",
    ):
        daily[column] = daily[column].fillna(0.0).astype("Float64")
    return daily.sort_values("start_at").reset_index(drop=True)


def _aggregate_workouts_by_cycle(cycles: pd.DataFrame, workouts: pd.DataFrame) -> pd.DataFrame:
    assigned_rows: list[dict[str, Any]] = []
    for workout in workouts.itertuples(index=False):
        workout_start = workout.start_at
        if pd.isna(workout_start):
            continue
        candidates = cycles.loc[
            (cycles["start_at"] <= workout_start)
            & (cycles["end_at"].isna() | (workout_start < cycles["end_at"]))
        ]
        if candidates.empty:
            continue
        cycle_id = candidates.sort_values("start_at").iloc[-1]["cycle_id"]
        end_at = cast(pd.Timestamp, workout.end_at)
        start_at = cast(pd.Timestamp, workout.start_at)
        duration = (end_at - start_at).total_seconds() / 3600.0 if not pd.isna(end_at) else None
        assigned_rows.append(
            {
                "cycle_id": cycle_id,
                "strain": workout.strain,
                "kilojoule": workout.kilojoule,
                "duration_hours": duration,
            }
        )

    columns = [
        "cycle_id",
        "workout_count",
        "workout_total_strain",
        "workout_max_strain",
        "workout_total_kilojoule",
        "workout_total_duration_hours",
    ]
    if not assigned_rows:
        return pd.DataFrame(columns=columns)
    assigned = pd.DataFrame(assigned_rows)
    grouped = assigned.groupby("cycle_id", as_index=False, dropna=False).agg(
        workout_count=("cycle_id", "size"),
        workout_total_strain=("strain", "sum"),
        workout_max_strain=("strain", "max"),
        workout_total_kilojoule=("kilojoule", "sum"),
        workout_total_duration_hours=("duration_hours", "sum"),
    )
    return grouped[columns]
