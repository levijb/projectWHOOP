"""Personal-baseline anomaly flags; these detect deviations, not illness diagnoses."""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from .data import ANOMALY_COLUMNS


class RollingAnomalyDetector:
    """Prior-only rolling median/MAD; each scored row is excluded from its own baseline."""

    def __init__(self, window: int = 28, min_periods: int = 14, k: float = 3.5) -> None:
        if not 2 <= min_periods <= window or k <= 0:
            raise ValueError("Invalid rolling anomaly configuration")
        self.window, self.min_periods, self.k = window, min_periods, k
        self.history = pd.DataFrame(columns=list(ANOMALY_COLUMNS))

    def fit(self, history: pd.DataFrame) -> RollingAnomalyDetector:
        self.history = history.loc[:, list(ANOMALY_COLUMNS)].astype(float).tail(self.window).copy()
        return self

    def score(self, features: pd.DataFrame) -> pd.DataFrame:
        values = features.loc[:, list(ANOMALY_COLUMNS)].astype(float)
        combined = pd.concat([self.history, values], ignore_index=True)
        # Floors prevent a perfectly constant baseline from dividing by zero.
        floors = np.array([1.0, 1.0, 0.05, 0.1])
        scores = []
        for index in range(len(self.history), len(combined)):
            baseline = combined.iloc[max(0, index - self.window) : index]
            median = baseline.median()
            mad = (baseline - median).abs().median()
            scale = np.maximum(1.4826 * mad.to_numpy(), floors)
            z = (combined.iloc[index] - median).abs().to_numpy() / scale
            z[baseline.count().to_numpy() < self.min_periods] = np.nan
            scores.append(z)
        return pd.DataFrame(scores, columns=list(ANOMALY_COLUMNS), index=features.index)

    def predict(self, features: pd.DataFrame) -> NDArray[np.bool_]:
        return self.score(features).gt(self.k).any(axis=1).to_numpy(dtype=bool)

    def get_params(self, deep: bool = True) -> dict[str, float | int]:
        return {"window": self.window, "min_periods": self.min_periods, "k": self.k}


class IsolationAnomalyDetector:
    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self.pipeline = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                (
                    "forest",
                    IsolationForest(
                        n_estimators=100, contamination=0.03, random_state=seed, n_jobs=1
                    ),
                ),
            ]
        )

    def fit(self, history: pd.DataFrame) -> IsolationAnomalyDetector:
        self.pipeline.fit(history.loc[:, list(ANOMALY_COLUMNS)])
        return self

    def predict(self, features: pd.DataFrame) -> NDArray[np.bool_]:
        return np.asarray(
            self.pipeline.predict(features.loc[:, list(ANOMALY_COLUMNS)]) == -1, dtype=bool
        )

    def get_params(self, deep: bool = True) -> dict[str, float | int]:
        return {"seed": self.seed, "n_estimators": 100, "contamination": 0.03}
