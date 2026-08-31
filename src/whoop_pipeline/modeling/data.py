"""Single-person, next-cycle targets from the existing dbt mart, with no future filling."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# All twelve exist in mart_daily_features.sql. IDs/timestamps are never model inputs.
FEATURE_COLUMNS = (
    "day_of_week",
    "cycle_strain",
    "recovery_score",
    "hrv_rmssd_milli",
    "sleep_debt_need_hours",
    "recovery_score_7d_avg",
    "cycle_strain_7d_avg",
    "hrv_rmssd_milli_7d_avg",
    "prior_day_strain",
    "sleep_debt_7d_avg_hours",
    "sleep_debt_trend_hours",
    "days_since_last_low_strain_day",
)
ANOMALY_COLUMNS = (
    "hrv_rmssd_milli",
    "resting_heart_rate",
    "skin_temp_celsius",
    "respiratory_rate",
)
LOW_RECOVERY_THRESHOLD = 33.0


def ordered_history(history: pd.DataFrame) -> pd.DataFrame:
    """Reject ambiguous/multi-user input rather than silently mixing personal baselines."""
    required = {"cycle_id", "user_id", "start_at", *FEATURE_COLUMNS}
    if missing := required - set(history.columns):
        raise ValueError(f"Missing mart columns: {sorted(missing)}")
    result = history.copy()
    result["start_at"] = pd.to_datetime(result["start_at"], utc=True, errors="raise")
    if result[["cycle_id", "user_id", "start_at"]].isna().any().any():
        raise ValueError("Cycle identity and start_at must be present")
    if result["user_id"].nunique() > 1:
        raise ValueError("Models require a single user's history")
    if result["cycle_id"].duplicated().any() or result["start_at"].duplicated().any():
        raise ValueError("Cycle IDs and timestamps must be unique")
    for name in FEATURE_COLUMNS:
        result[name] = pd.to_numeric(result[name], errors="raise").astype(float)
        if np.isinf(result[name]).any():
            raise ValueError(f"Infinite feature: {name}")
    scores = result["recovery_score"].dropna()
    if not scores.between(0, 100).all():
        raise ValueError("Recovery scores must be between 0 and 100")
    return result.sort_values("start_at").reset_index(drop=True)


@dataclass(frozen=True)
class SupervisedData:
    features: pd.DataFrame
    target: pd.Series[float]
    low_recovery: pd.Series[int]
    origins: pd.DataFrame


def supervised_data(history: pd.DataFrame) -> SupervisedData:
    """Label N from N+1 BEFORE dropping unknown outcomes; never skip across a pending cycle."""
    ordered = ordered_history(history)
    target = ordered["recovery_score"].shift(-1)
    origins = ordered[["cycle_id", "start_at"]].copy()
    origins["target_cycle_id"] = ordered["cycle_id"].shift(-1)
    origins["target_start_at"] = ordered["start_at"].shift(-1)
    known = target.notna()
    return SupervisedData(
        ordered.loc[known, list(FEATURE_COLUMNS)].reset_index(drop=True),
        target.loc[known].reset_index(drop=True),
        (target.loc[known] <= LOW_RECOVERY_THRESHOLD).astype(int).reset_index(drop=True),
        origins.loc[known].reset_index(drop=True),
    )


def require_history(data: SupervisedData, *, min_pairs: int = 90) -> None:
    """60 training pairs plus multiple future validation windows is the minimum, not proof."""
    if len(data.target) < min_pairs:
        raise ValueError(f"Insufficient history: need at least {min_pairs} labeled cycle pairs")
    span = data.origins["target_start_at"].max() - data.origins["start_at"].min()
    if span < pd.Timedelta(days=89):
        raise ValueError("Insufficient history: need at least 90 calendar days of coverage")
