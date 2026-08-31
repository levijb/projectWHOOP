"""Forecast persistence on the existing injected Postgres/SQLite backend, with atomic retries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .postgres_backend import PostgresBackend
from .postgres_models import PREDICTIONS_TABLE


@dataclass(frozen=True)
class Prediction:
    cycle_id: int
    model_name: str
    model_version: str
    origin_at: datetime
    created_at: datetime
    predicted_value: float
    ci_lower: float
    ci_upper: float


class PredictionStore:
    def __init__(self, backend: PostgresBackend) -> None:
        self.backend = backend

    def save(self, prediction: Prediction) -> bool:
        """First forecast wins; ON CONFLICT keeps original version/value on replay."""
        dialect = self.backend.engine.dialect.name
        insert = sqlite_insert if dialect == "sqlite" else postgres_insert
        statement = (
            insert(PREDICTIONS_TABLE)
            .values(**asdict(prediction))
            .on_conflict_do_nothing(
                index_elements=["cycle_id", "model_name"],
            )
        )
        with self.backend.engine.begin() as connection:
            return bool(connection.execute(statement).rowcount)

    def records(self, model_name: str) -> list[dict[str, Any]]:
        with self.backend.engine.connect() as connection:
            result = connection.execute(
                select(PREDICTIONS_TABLE)
                .where(
                    PREDICTIONS_TABLE.c.model_name == model_name,
                )
                .order_by(PREDICTIONS_TABLE.c.origin_at)
            )
            return [dict(row) for row in result.mappings()]

    def settle(self, history: pd.DataFrame, model_name: str) -> None:
        """Resolve only the immediate next cycle, even if its recovery remains pending.

        Repeated pulls may correct scores. Only outcome fields change; issued forecasts stay
        immutable. No inferred cycle_id arithmetic and no leap over null recovery scores.
        """
        ordered = history.sort_values("start_at").reset_index(drop=True)
        pairs = {
            int(ordered.iloc[i]["cycle_id"]): ordered.iloc[i + 1] for i in range(len(ordered) - 1)
        }
        with self.backend.engine.begin() as connection:
            rows = (
                connection.execute(
                    select(PREDICTIONS_TABLE).where(
                        PREDICTIONS_TABLE.c.model_name == model_name,
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                target = pairs.get(row["cycle_id"])
                if target is None:
                    continue
                # Predictions created after this outcome cycle started are not prospective.
                created = pd.Timestamp(row["created_at"])
                created = created.tz_localize("UTC") if created.tzinfo is None else created
                if created >= pd.Timestamp(target["start_at"]):
                    continue
                actual = (
                    None if pd.isna(target["recovery_score"]) else float(target["recovery_score"])
                )
                connection.execute(
                    update(PREDICTIONS_TABLE)
                    .where(
                        PREDICTIONS_TABLE.c.cycle_id == row["cycle_id"],
                        PREDICTIONS_TABLE.c.model_name == model_name,
                    )
                    .values(
                        target_cycle_id=int(target["cycle_id"]),
                        actual_value=actual,
                        error=None if actual is None else row["predicted_value"] - actual,
                    )
                )


def model_history_sql(*, postgres: bool) -> str:
    """No SELECT * ambiguity. Mart contains target; join only non-mart anomaly inputs."""
    prefix = "whoop." if postgres else ""
    return f"""
        WITH main_sleep AS (
            SELECT cycle_id, respiratory_rate,
                ROW_NUMBER() OVER (PARTITION BY cycle_id ORDER BY updated_at DESC, sleep_id) AS rank
            FROM {prefix}sleep WHERE NOT is_nap
        )
        SELECT m.*, c.end_at, r.resting_heart_rate, r.skin_temp_celsius, r.spo2_percentage,
               s.respiratory_rate
        FROM {prefix}mart_daily_features AS m
        JOIN {prefix}cycles AS c ON c.cycle_id = m.cycle_id
        LEFT JOIN {prefix}recovery AS r ON r.cycle_id = m.cycle_id
        LEFT JOIN main_sleep AS s ON s.cycle_id = m.cycle_id AND s.rank = 1
        ORDER BY m.start_at
    """


def read_postgres_history(backend: PostgresBackend) -> pd.DataFrame:
    with backend.engine.connect() as connection:
        return pd.read_sql(
            text(model_history_sql(postgres=connection.dialect.name == "postgresql")), connection
        )
