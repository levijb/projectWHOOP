"""Expanding chronological validation, with a purged cycle at supervised fold boundaries."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from sklearn.metrics import precision_score, r2_score, recall_score, roc_auc_score

from .data import SupervisedData


def expanding_splits(
    size: int,
    *,
    initial_train: int = 60,
    test_size: int = 14,
    gap: int = 1,
) -> Iterator[tuple[NDArray[np.int64], NDArray[np.int64]]]:
    if initial_train < 2 or test_size < 2 or gap < 0:
        raise ValueError("Invalid expanding-window configuration")
    if size < initial_train + gap + 2 * test_size:
        raise ValueError("Need at least two complete walk-forward validation folds")
    for start in range(initial_train + gap, size - test_size + 1, test_size):
        yield np.arange(start - gap), np.arange(start, start + test_size)


def regression_metrics(
    actual: NDArray[np.float64], predicted: NDArray[np.float64]
) -> dict[str, float]:
    if actual.shape != predicted.shape or not len(actual):
        raise ValueError("Metrics require equally sized nonempty arrays")
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ValueError("Metrics require finite values")
    errors = predicted - actual
    nonzero = actual != 0
    result = {
        "mae": float(np.mean(np.abs(errors))),
        "r2": float(r2_score(actual, predicted)),
        "mape_excluded_zeros": float((~nonzero).sum()),
    }
    # Zero recovery has no defined percentage error; count omissions instead of epsilon blowup.
    if nonzero.any():
        result["mape"] = float(np.mean(np.abs(errors[nonzero] / actual[nonzero])) * 100)
    return result


@dataclass
class ValidationResult:
    folds: list[dict[str, float]] = field(default_factory=list)
    residuals: list[float] = field(default_factory=list)  # actual - predicted, out of sample only
    skipped_folds: int = 0

    def aggregate(self) -> dict[str, float]:
        result = {"folds": float(len(self.folds)), "skipped_folds": float(self.skipped_folds)}
        for key in sorted({key for fold in self.folds for key in fold}):
            values = [fold[key] for fold in self.folds if key in fold]
            result[f"{key}_mean"] = float(np.mean(values))
            result[f"{key}_std"] = float(np.std(values))
            result[f"{key}_folds"] = float(len(values))
        return result


def validate_supervised(
    data: SupervisedData,
    factory: Callable[[], Any],
    *,
    classifier: bool = False,
) -> ValidationResult:
    result = ValidationResult()
    target = data.low_recovery if classifier else data.target
    for train, test in expanding_splits(len(target)):
        # An adjacent next-cycle label must not reach the first test feature's timestamp.
        if (
            data.origins.iloc[train]["target_start_at"].max()
            >= data.origins.iloc[test]["start_at"].min()
        ):
            raise ValueError("Training labels overlap the validation feature window")
        if classifier and target.iloc[train].nunique() < 2:
            result.skipped_folds += 1
            continue
        model = factory()
        model.fit(data.features.iloc[train], target.iloc[train])
        actual = np.asarray(target.iloc[test], dtype=np.float64)
        if classifier:
            probability = model.predict_proba(data.features.iloc[test])[:, 1]
            predicted = probability >= 0.5
            metrics = {
                "precision": float(precision_score(actual, predicted, zero_division=0)),
                "recall": float(recall_score(actual, predicted, zero_division=0)),
                "positive_count": float(actual.sum()),
            }
            if len(np.unique(actual)) == 2:
                metrics["auc"] = float(roc_auc_score(actual, probability))
        else:
            predicted = model.predict(data.features.iloc[test])
            metrics = regression_metrics(actual, predicted)
            result.residuals.extend((actual - predicted).tolist())
        result.folds.append(metrics)
    return result


def validate_anomaly(history: pd.DataFrame, factory: Callable[[], Any]) -> ValidationResult:
    result = ValidationResult()
    for train, test in expanding_splits(len(history), gap=0):
        model = factory()
        model.fit(history.iloc[train])
        flags = model.predict(history.iloc[test])
        result.folds.append(
            {"flag_rate": float(np.mean(flags)), "evaluated_rows": float(len(test))}
        )
    return result
