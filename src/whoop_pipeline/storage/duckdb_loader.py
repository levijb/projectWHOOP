"""Validated and idempotent DuckDB gold loading."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from whoop_pipeline.validation.schemas import validate_silver_frames

DEFAULT_DATABASE_PATH = Path("data/processed/whoop.db")
TABLE_KEYS = {
    "cycles": "cycle_id",
    "recovery": "cycle_id",
    "sleep": "sleep_id",
    "workouts": "workout_id",
}


def load_silver_frames(
    cycles_df: pd.DataFrame,
    recovery_df: pd.DataFrame,
    sleep_df: pd.DataFrame,
    workouts_df: pd.DataFrame,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> Path:
    """Validate then upsert four silver frames and recreate ``daily_summary``.

    Idempotency is implemented as delete-then-insert by the stable API identifier, all inside
    one transaction. Reprocessing corrected records updates them without duplicating history.
    """
    frames = {
        "cycles": cycles_df,
        "recovery": recovery_df,
        "sleep": sleep_df,
        "workouts": workouts_df,
    }
    validate_silver_frames(frames)
    output_path = Path(database_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(str(output_path))
    try:
        connection.execute("BEGIN TRANSACTION")
        for table_name, frame in frames.items():
            _upsert_frame(connection, table_name, TABLE_KEYS[table_name], frame)
        _create_daily_summary_view(connection)
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return output_path


def _upsert_frame(
    connection: duckdb.DuckDBPyConnection,
    table_name: str,
    key_column: str,
    frame: pd.DataFrame,
) -> None:
    staging_name = f"staging_{table_name}"
    connection.register(staging_name, frame)
    try:
        connection.execute(
            f'CREATE TABLE IF NOT EXISTS "{table_name}" AS '
            f'SELECT * FROM "{staging_name}" WHERE FALSE'
        )
        if not frame.empty:
            connection.execute(
                f'DELETE FROM "{table_name}" WHERE "{key_column}" IN '
                f'(SELECT "{key_column}" FROM "{staging_name}")'
            )
            connection.execute(f'INSERT INTO "{table_name}" SELECT * FROM "{staging_name}"')
    finally:
        connection.unregister(staging_name)


def _create_daily_summary_view(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE OR REPLACE VIEW daily_summary AS
        WITH ranked_main_sleep AS (
            SELECT
                *,
                ROW_NUMBER() OVER (PARTITION BY cycle_id ORDER BY updated_at DESC) AS row_number
            FROM sleep
            WHERE NOT is_nap
        ),
        workout_assignments AS (
            SELECT
                c.cycle_id,
                w.*,
                ROW_NUMBER() OVER (
                    PARTITION BY w.workout_id
                    ORDER BY c.start_at DESC
                ) AS cycle_rank
            FROM workouts AS w
            INNER JOIN cycles AS c
                ON w.start_at >= c.start_at
                AND (c.end_at IS NULL OR w.start_at < c.end_at)
        ),
        workout_by_cycle AS (
            SELECT
                c.cycle_id,
                COUNT(w.workout_id) AS workout_count,
                COALESCE(SUM(w.strain), 0.0) AS workout_total_strain,
                COALESCE(MAX(w.strain), 0.0) AS workout_max_strain,
                COALESCE(SUM(w.kilojoule), 0.0) AS workout_total_kilojoule,
                COALESCE(
                    SUM(DATE_DIFF('millisecond', w.start_at, w.end_at)) / 3600000.0,
                    0.0
                ) AS workout_total_duration_hours
            FROM cycles AS c
            LEFT JOIN workout_assignments AS w
                ON c.cycle_id = w.cycle_id AND w.cycle_rank = 1
            GROUP BY c.cycle_id
        )
        SELECT
            c.cycle_id,
            c.user_id,
            c.start_at,
            c.end_at,
            c.timezone_offset,
            c.score_state AS cycle_score_state,
            c.strain AS cycle_strain,
            c.kilojoule AS cycle_kilojoule,
            c.average_heart_rate AS cycle_average_heart_rate,
            c.max_heart_rate AS cycle_max_heart_rate,
            r.score_state AS recovery_score_state,
            r.user_calibrating,
            r.recovery_score,
            r.resting_heart_rate,
            r.hrv_rmssd_milli,
            r.spo2_percentage,
            r.skin_temp_celsius,
            s.sleep_id,
            s.score_state AS sleep_score_state,
            s.total_in_bed_hours,
            s.total_awake_hours,
            s.total_no_data_hours,
            s.total_light_sleep_hours,
            s.total_slow_wave_sleep_hours,
            s.total_rem_sleep_hours,
            s.sleep_cycle_count,
            s.disturbance_count,
            s.baseline_sleep_need_hours,
            s.sleep_debt_need_hours,
            s.recent_strain_need_hours,
            s.recent_nap_need_hours,
            s.respiratory_rate,
            s.sleep_performance_percentage,
            s.sleep_consistency_percentage,
            s.sleep_efficiency_percentage,
            w.workout_count,
            w.workout_total_strain,
            w.workout_max_strain,
            w.workout_total_kilojoule,
            w.workout_total_duration_hours
        FROM cycles AS c
        LEFT JOIN recovery AS r USING (cycle_id)
        LEFT JOIN ranked_main_sleep AS s
            ON c.cycle_id = s.cycle_id AND s.row_number = 1
        LEFT JOIN workout_by_cycle AS w USING (cycle_id)
        """
    )
