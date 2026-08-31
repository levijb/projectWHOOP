"""Ridge reference, incremental SGD serving candidate, and explicitly unpromoted challengers."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge, SGDRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

Algorithm = Literal["ridge", "sgd", "gradient_boosting"]


class RecoveryRegressor:
    """Frozen training preprocessing keeps the SGD coefficient coordinate system stable."""

    def __init__(self, algorithm: Algorithm = "sgd", seed: int = 42) -> None:
        self.algorithm = algorithm
        self.seed = seed
        self.preprocessor = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("scaler", StandardScaler()),
            ]
        )
        self.estimator: Any
        if algorithm == "ridge":
            self.estimator = Ridge(alpha=10.0, solver="svd")
        elif algorithm == "sgd":
            self.estimator = SGDRegressor(
                loss="squared_error",
                penalty="l2",
                alpha=0.01,
                learning_rate="invscaling",
                eta0=0.01,
                max_iter=2000,
                tol=1e-4,
                random_state=seed,
                shuffle=False,
            )
        elif algorithm == "gradient_boosting":
            self.estimator = GradientBoostingRegressor(
                n_estimators=80,
                max_depth=2,
                learning_rate=0.05,
                random_state=seed,
            )
        else:
            raise ValueError("Unknown recovery algorithm")
        self.metadata: dict[str, str] = {}
        self.residuals: list[float] = []

    def fit(self, features: pd.DataFrame, target: pd.Series[float]) -> RecoveryRegressor:
        transformed = self.preprocessor.fit_transform(features)
        self.estimator.fit(transformed, target)
        return self

    def partial_fit(self, features: pd.DataFrame, target: pd.Series[float]) -> RecoveryRegressor:
        if self.algorithm != "sgd":
            raise ValueError("Only SGD supports incremental updates")
        # Deliberately do not refit/partial_fit the scaler or imputer on new observations.
        self.estimator.partial_fit(self.preprocessor.transform(features), target)
        return self

    def predict(self, features: pd.DataFrame) -> NDArray[np.float64]:
        return np.asarray(
            np.clip(self.estimator.predict(self.preprocessor.transform(features)), 0, 100),
            dtype=float,
        )

    def get_params(self, deep: bool = True) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "seed": self.seed,
            **self.estimator.get_params(deep=deep),
        }


def low_recovery_classifier() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(class_weight="balanced", C=1.0, max_iter=2000, random_state=42),
            ),
        ]
    )
