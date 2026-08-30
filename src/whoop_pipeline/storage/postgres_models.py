"""SQLAlchemy Core metadata for the private Postgres gold/state/token schema.

Names and nullability mirror the Pandera silver contracts. Alembic revisions are frozen
separately; migration tests compare the resulting schema with this runtime metadata.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
)

from whoop_pipeline.storage.database import POSTGRES_SCHEMA

METADATA = MetaData(schema=POSTGRES_SCHEMA)

CYCLES_TABLE = Table(
    "cycles",
    METADATA,
    Column("cycle_id", BigInteger, primary_key=True),
    Column("user_id", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("start_at", DateTime(timezone=True), nullable=False),
    Column("end_at", DateTime(timezone=True), nullable=True),
    Column("timezone_offset", String, nullable=False),
    Column("score_state", String, nullable=False),
    Column("strain", Float, nullable=True),
    Column("kilojoule", Float, nullable=True),
    Column("average_heart_rate", Integer, nullable=True),
    Column("max_heart_rate", Integer, nullable=True),
)

RECOVERY_TABLE = Table(
    "recovery",
    METADATA,
    Column("cycle_id", BigInteger, primary_key=True),
    Column("sleep_id", String, nullable=False),
    Column("user_id", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("score_state", String, nullable=False),
    Column("user_calibrating", Boolean, nullable=True),
    Column("recovery_score", Integer, nullable=True),
    Column("resting_heart_rate", Integer, nullable=True),
    Column("hrv_rmssd_milli", Float, nullable=True),
    Column("spo2_percentage", Float, nullable=True),
    Column("skin_temp_celsius", Float, nullable=True),
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

SLEEP_TABLE = Table(
    "sleep",
    METADATA,
    Column("sleep_id", String, primary_key=True),
    Column("cycle_id", BigInteger, nullable=False),
    Column("user_id", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("start_at", DateTime(timezone=True), nullable=False),
    Column("end_at", DateTime(timezone=True), nullable=False),
    Column("timezone_offset", String, nullable=False),
    Column("is_nap", Boolean, nullable=False),
    Column("score_state", String, nullable=False),
    *(Column(name, Float, nullable=True) for name in SLEEP_DURATION_COLUMNS),
    # Recent naps reduce current need, so this one duration can legitimately be negative.
    Column("recent_nap_need_hours", Float, nullable=True),
    Column("sleep_cycle_count", Integer, nullable=True),
    Column("disturbance_count", Integer, nullable=True),
    Column("respiratory_rate", Float, nullable=True),
    Column("sleep_performance_percentage", Float, nullable=True),
    Column("sleep_consistency_percentage", Float, nullable=True),
    Column("sleep_efficiency_percentage", Float, nullable=True),
)

WORKOUTS_TABLE = Table(
    "workouts",
    METADATA,
    Column("workout_id", String, primary_key=True),
    Column("user_id", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("start_at", DateTime(timezone=True), nullable=False),
    Column("end_at", DateTime(timezone=True), nullable=False),
    Column("timezone_offset", String, nullable=False),
    Column("sport_name", String, nullable=False),
    Column("score_state", String, nullable=False),
    Column("strain", Float, nullable=True),
    Column("average_heart_rate", Integer, nullable=True),
    Column("max_heart_rate", Integer, nullable=True),
    Column("kilojoule", Float, nullable=True),
    Column("percent_recorded", Float, nullable=True),
    Column("distance_meter", Float, nullable=True),
    Column("altitude_gain_meter", Float, nullable=True),
    Column("altitude_change_meter", Float, nullable=True),
    *(Column(f"zone_{zone}_hours", Float, nullable=True) for zone in range(6)),
)

GOLD_TABLES: dict[str, tuple[Table, str]] = {
    "cycles": (CYCLES_TABLE, "cycle_id"),
    "recovery": (RECOVERY_TABLE, "cycle_id"),
    "sleep": (SLEEP_TABLE, "sleep_id"),
    "workouts": (WORKOUTS_TABLE, "workout_id"),
}

# SQLAlchemy Date round-trips on both Postgres and SQLite.
SYNC_STATE_TABLE = Table(
    "sync_state",
    METADATA,
    Column("id", Integer, primary_key=True),
    Column("last_synced_date", Date, nullable=False),
)

WHOOP_TOKENS_TABLE = Table(
    "whoop_tokens",
    METADATA,
    Column("id", Integer, primary_key=True),
    Column("access_token", String, nullable=False),
    Column("refresh_token", String, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
