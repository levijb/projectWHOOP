"""Seeded synthetic physiology through the real mart SQL; never reads personal data."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import duckdb
import numpy as np
import pandas as pd
from jinja2 import Environment, StrictUndefined


def synthetic_daily(days: int = 180, seed: int = 42) -> pd.DataFrame:
    """AR(1) stress with noisy strain/sleep and occasional stress shocks; no clinical fit.

    Recovery depends on prior stress, strain, debt and its own lag. Physiological fields share
    a latent stress signal plus independent noise. Values are bounded to plausible ranges.
    The seed fixes the draw order; labels are never handpicked or balanced after generation.
    """
    if days < 60:
        raise ValueError("Synthetic experiments require at least 60 days")
    rng = np.random.default_rng(seed)
    rows = []
    stress, recovery, strain, debt = 0.0, 65.0, 9.0, 0.8
    for index in range(days):
        stress = 0.78 * stress + rng.normal(0, 0.65) + (rng.random() < 0.08) * 2.8
        recovery = float(
            np.clip(
                56
                + 0.35 * (recovery - 56)
                - 9 * stress
                - 1.2 * (strain - 10)
                - 3 * debt
                + rng.normal(0, 7),
                1,
                99,
            )
        )
        strain = float(
            np.clip(10 + 3 * np.sin(2 * np.pi * index / 7) - 0.5 * stress + rng.normal(0, 2), 0, 20)
        )
        debt = float(np.clip(0.65 * debt + 0.10 * max(strain - 8, 0) + rng.normal(0, 0.25), 0, 4))
        start = pd.Timestamp("2025-01-01", tz="UTC") + pd.Timedelta(days=index)
        rows.append(
            {
                "cycle_id": 1_000_000 + index,
                "user_id": 999_001,
                "start_at": start,
                "end_at": start + pd.Timedelta(days=1),
                "cycle_strain": strain,
                "recovery_score": recovery,
                "hrv_rmssd_milli": float(np.clip(65 - 7 * stress + rng.normal(0, 3), 10, 130)),
                "resting_heart_rate": float(
                    np.clip(56 + 2.5 * stress + rng.normal(0, 1.5), 40, 95)
                ),
                "skin_temp_celsius": float(33.4 + 0.12 * stress + rng.normal(0, 0.08)),
                "respiratory_rate": float(15 + 0.25 * stress + rng.normal(0, 0.15)),
                "spo2_percentage": float(
                    np.clip(97.5 - 0.2 * stress + rng.normal(0, 0.3), 90, 100)
                ),
                "sleep_debt_need_hours": debt,
                "sleep_performance_percentage": float(
                    np.clip(95 - 8 * debt + rng.normal(0, 3), 20, 100)
                ),
                "total_in_bed_hours": float(np.clip(8.5 - 0.5 * debt + rng.normal(0, 0.4), 3, 11)),
            }
        )
    return pd.DataFrame(rows)


def synthetic_history(
    days: int = 180, seed: int = 42, *, dbt_dir: Path | None = None
) -> pd.DataFrame:
    """Execute repository mart and day-of-week macro with a synthetic in-memory source.

    This small Jinja harness supplies source()/target only; feature formulas are never
    duplicated. Integration tests additionally exercise the actual dbt build command.
    """
    project = dbt_dir or Path(__file__).resolve().parents[3] / "dbt"
    environment = Environment(undefined=StrictUndefined)
    environment.globals.update(target=SimpleNamespace(type="duckdb"))
    macro = environment.from_string(
        (project / "macros/day_of_week.sql").read_text(encoding="utf-8")
    ).module
    environment.globals.update(
        day_of_week=macro.__dict__["day_of_week"], source=lambda *args: "daily_summary"
    )
    sql = environment.from_string(
        (project / "models/marts/mart_daily_features.sql").read_text(encoding="utf-8")
    ).render()
    daily = synthetic_daily(days, seed)
    with duckdb.connect(":memory:") as connection:
        connection.execute("SET TimeZone='UTC'")
        connection.register("daily_summary", daily)
        mart = connection.execute(sql).fetchdf()
    extras = daily[
        [
            "cycle_id",
            "end_at",
            "resting_heart_rate",
            "skin_temp_celsius",
            "respiratory_rate",
            "spo2_percentage",
        ]
    ]
    return (
        mart.merge(extras, on="cycle_id", validate="one_to_one")
        .sort_values("start_at")
        .reset_index(drop=True)
    )
