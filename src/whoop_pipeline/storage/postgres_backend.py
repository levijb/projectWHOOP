"""Validated gold, checkpoint, and token persistence in an Alembic-managed schema.

Tests run the same transactional delete/insert operations on a disposable SQLite database.
Production uses the private whoop schema. No runtime method creates tables.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import Connection, Engine

from whoop_pipeline.oauth import WhoopTokenPair
from whoop_pipeline.storage.database import POSTGRES_SCHEMA, postgres_url
from whoop_pipeline.storage.postgres_models import GOLD_TABLES, SYNC_STATE_TABLE, WHOOP_TOKENS_TABLE
from whoop_pipeline.validation.schemas import validate_silver_frames


class PostgresBackend:
    """Lazy SQLAlchemy backend. SQLite URLs are supported only as local test substitutes.

    Explicit construction is dependency injection, not ambient environment selection. The
    Dagster and migration entry points separately require WHOOP_PIPELINE_USE_POSTGRES.
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            if self._database_url.startswith("sqlite:"):
                self._engine = create_engine(
                    self._database_url,
                    hide_parameters=True,
                    execution_options={"schema_translate_map": {POSTGRES_SCHEMA: None}},
                )
            else:
                self._engine = create_engine(
                    postgres_url(self._database_url), hide_parameters=True, pool_pre_ping=True
                )
        return self._engine

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def load_gold(
        self,
        cycles_df: pd.DataFrame,
        recovery_df: pd.DataFrame,
        sleep_df: pd.DataFrame,
        workouts_df: pd.DataFrame,
        *,
        last_synced_date: date | None = None,
    ) -> None:
        """Validate all frames, then commit all four tables and the checkpoint together.

        Matches Phase 1's delete-then-insert-by-id semantics. A failed validation, insert,
        or checkpoint write cannot discard existing gold or advance the sync window.
        """
        frames = {
            "cycles": cycles_df,
            "recovery": recovery_df,
            "sleep": sleep_df,
            "workouts": workouts_df,
        }
        validate_silver_frames(frames)
        with self.engine.begin() as connection:
            for name, frame in frames.items():
                table, key_column = GOLD_TABLES[name]
                # Chunk statements for SQLite's parameter limit and large initial backfills.
                for offset in range(0, len(frame), 500):
                    records = _sql_records(frame.iloc[offset : offset + 500])
                    keys = [row[key_column] for row in records]
                    connection.execute(delete(table).where(table.c[key_column].in_(keys)))
                    connection.execute(table.insert(), records)
            if last_synced_date is not None:
                self._write_sync_state(connection, last_synced_date)

    def read_sync_state(self) -> date | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(SYNC_STATE_TABLE.c.last_synced_date).where(SYNC_STATE_TABLE.c.id == 1)
            ).fetchone()
        return row[0] if row is not None else None

    @staticmethod
    def _write_sync_state(connection: Connection, last_synced_date: date) -> None:
        connection.execute(delete(SYNC_STATE_TABLE).where(SYNC_STATE_TABLE.c.id == 1))
        connection.execute(
            SYNC_STATE_TABLE.insert().values(id=1, last_synced_date=last_synced_date)
        )

    def update_sync_state(self, last_synced_date: date) -> None:
        with self.engine.begin() as connection:
            self._write_sync_state(connection, last_synced_date)

    def read_tokens(self) -> WhoopTokenPair | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    WHOOP_TOKENS_TABLE.c.access_token,
                    WHOOP_TOKENS_TABLE.c.refresh_token,
                    WHOOP_TOKENS_TABLE.c.expires_at,
                ).where(WHOOP_TOKENS_TABLE.c.id == 1)
            ).fetchone()
        if row is None:
            return None
        access_token, refresh_token, expires_at = row
        if expires_at.tzinfo is None:  # SQLite does not retain timezone metadata.
            expires_at = expires_at.replace(tzinfo=UTC)
        return WhoopTokenPair(
            access_token=access_token, refresh_token=refresh_token, expires_at=expires_at
        )

    def save_tokens(self, tokens: WhoopTokenPair) -> None:
        with self.engine.begin() as connection:
            connection.execute(delete(WHOOP_TOKENS_TABLE).where(WHOOP_TOKENS_TABLE.c.id == 1))
            connection.execute(
                WHOOP_TOKENS_TABLE.insert().values(
                    id=1,
                    access_token=tokens.access_token,
                    refresh_token=tokens.refresh_token,
                    expires_at=tokens.expires_at.astimezone(UTC),
                    updated_at=datetime.now(UTC),
                )
            )


def _sql_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert nullable pandas values to DBAPI values, retaining aware timestamps."""
    return [
        {
            str(key): None
            if pd.isna(value)
            else value.to_pydatetime()
            if isinstance(value, pd.Timestamp)
            else value
            for key, value in row.items()
        }
        for row in frame.to_dict(orient="records")
    ]
