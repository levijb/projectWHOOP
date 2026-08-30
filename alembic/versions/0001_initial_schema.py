"""Initial gold/sync-state/token schema.

Revision ID: 0001
Revises:
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    schema = "whoop" if op.get_bind().dialect.name == "postgresql" else None
    if schema:
        op.execute("CREATE SCHEMA whoop")
        op.execute("REVOKE ALL ON SCHEMA whoop FROM PUBLIC")
    op.create_table(
        "cycles",
        sa.Column("cycle_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone_offset", sa.String(), nullable=False),
        sa.Column("score_state", sa.String(), nullable=False),
        sa.Column("strain", sa.Float(), nullable=True),
        sa.Column("kilojoule", sa.Float(), nullable=True),
        sa.Column("average_heart_rate", sa.Integer(), nullable=True),
        sa.Column("max_heart_rate", sa.Integer(), nullable=True),
        schema=schema,
    )

    op.create_table(
        "recovery",
        sa.Column("cycle_id", sa.BigInteger(), primary_key=True),
        sa.Column("sleep_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score_state", sa.String(), nullable=False),
        sa.Column("user_calibrating", sa.Boolean(), nullable=True),
        sa.Column("recovery_score", sa.Integer(), nullable=True),
        sa.Column("resting_heart_rate", sa.Integer(), nullable=True),
        sa.Column("hrv_rmssd_milli", sa.Float(), nullable=True),
        sa.Column("spo2_percentage", sa.Float(), nullable=True),
        sa.Column("skin_temp_celsius", sa.Float(), nullable=True),
        schema=schema,
    )

    op.create_table(
        "sleep",
        sa.Column("sleep_id", sa.String(), primary_key=True),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone_offset", sa.String(), nullable=False),
        sa.Column("is_nap", sa.Boolean(), nullable=False),
        sa.Column("score_state", sa.String(), nullable=False),
        sa.Column("total_in_bed_hours", sa.Float(), nullable=True),
        sa.Column("total_awake_hours", sa.Float(), nullable=True),
        sa.Column("total_no_data_hours", sa.Float(), nullable=True),
        sa.Column("total_light_sleep_hours", sa.Float(), nullable=True),
        sa.Column("total_slow_wave_sleep_hours", sa.Float(), nullable=True),
        sa.Column("total_rem_sleep_hours", sa.Float(), nullable=True),
        sa.Column("baseline_sleep_need_hours", sa.Float(), nullable=True),
        sa.Column("sleep_debt_need_hours", sa.Float(), nullable=True),
        sa.Column("recent_strain_need_hours", sa.Float(), nullable=True),
        sa.Column("recent_nap_need_hours", sa.Float(), nullable=True),
        sa.Column("sleep_cycle_count", sa.Integer(), nullable=True),
        sa.Column("disturbance_count", sa.Integer(), nullable=True),
        sa.Column("respiratory_rate", sa.Float(), nullable=True),
        sa.Column("sleep_performance_percentage", sa.Float(), nullable=True),
        sa.Column("sleep_consistency_percentage", sa.Float(), nullable=True),
        sa.Column("sleep_efficiency_percentage", sa.Float(), nullable=True),
        schema=schema,
    )

    op.create_table(
        "workouts",
        sa.Column("workout_id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone_offset", sa.String(), nullable=False),
        sa.Column("sport_name", sa.String(), nullable=False),
        sa.Column("score_state", sa.String(), nullable=False),
        sa.Column("strain", sa.Float(), nullable=True),
        sa.Column("average_heart_rate", sa.Integer(), nullable=True),
        sa.Column("max_heart_rate", sa.Integer(), nullable=True),
        sa.Column("kilojoule", sa.Float(), nullable=True),
        sa.Column("percent_recorded", sa.Float(), nullable=True),
        sa.Column("distance_meter", sa.Float(), nullable=True),
        sa.Column("altitude_gain_meter", sa.Float(), nullable=True),
        sa.Column("altitude_change_meter", sa.Float(), nullable=True),
        sa.Column("zone_0_hours", sa.Float(), nullable=True),
        sa.Column("zone_1_hours", sa.Float(), nullable=True),
        sa.Column("zone_2_hours", sa.Float(), nullable=True),
        sa.Column("zone_3_hours", sa.Float(), nullable=True),
        sa.Column("zone_4_hours", sa.Float(), nullable=True),
        sa.Column("zone_5_hours", sa.Float(), nullable=True),
        schema=schema,
    )

    op.create_table(
        "sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_synced_date", sa.Date(), nullable=False),
        schema=schema,
    )

    op.create_table(
        "whoop_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("access_token", sa.String(), nullable=False),
        sa.Column("refresh_token", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=schema,
    )

    # Postgres DDL is generated offline and the view's portable SQL is cross-checked on
    # fixture DuckDB. SQLite does not implement this view. A live Postgres smoke test
    # remains necessary for dialect/provider behavior and permissions.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE VIEW whoop.daily_summary AS
            WITH ranked_main_sleep AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (PARTITION BY cycle_id ORDER BY updated_at DESC) AS row_number
                FROM whoop.sleep
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
                FROM whoop.workouts AS w
                INNER JOIN whoop.cycles AS c
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
                        SUM(EXTRACT(EPOCH FROM (w.end_at - w.start_at)) * 1000) / 3600000.0,
                        0.0
                    ) AS workout_total_duration_hours
                FROM whoop.cycles AS c
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
            FROM whoop.cycles AS c
            LEFT JOIN whoop.recovery AS r USING (cycle_id)
            LEFT JOIN ranked_main_sleep AS s
                ON c.cycle_id = s.cycle_id AND s.row_number = 1
            LEFT JOIN workout_by_cycle AS w USING (cycle_id)
            """
        )


def downgrade() -> None:
    schema = "whoop" if op.get_bind().dialect.name == "postgresql" else None
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP VIEW IF EXISTS whoop.daily_summary")
    op.drop_table("whoop_tokens", schema=schema)
    op.drop_table("sync_state", schema=schema)
    op.drop_table("workouts", schema=schema)
    op.drop_table("sleep", schema=schema)
    op.drop_table("recovery", schema=schema)
    op.drop_table("cycles", schema=schema)

    if schema:
        op.execute("DROP SCHEMA whoop")
