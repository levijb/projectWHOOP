"""Reproducible synthetic-only experiment entry point; no database or WHOOP options."""

from __future__ import annotations

import argparse
import json
from functools import partial
from pathlib import Path
from typing import Any

from .anomaly import IsolationAnomalyDetector, RollingAnomalyDetector
from .data import FEATURE_COLUMNS, LOW_RECOVERY_THRESHOLD, require_history, supervised_data
from .models import Algorithm, RecoveryRegressor, low_recovery_classifier
from .monitoring import bootstrap_prediction_intervals
from .synthetic import synthetic_history
from .tracking import LocalModelRegistry
from .validation import validate_anomaly, validate_supervised


def run_synthetic_suite(root: Path, *, days: int = 180, seed: int = 42) -> dict[str, Any]:
    history = synthetic_history(days, seed)
    data = supervised_data(history)
    require_history(data)
    registry = LocalModelRegistry(root)
    report: dict[str, Any] = {
        "data_kind": "synthetic",
        "days": days,
        "seed": seed,
        "labeled_pairs": len(data.target),
        "features": list(FEATURE_COLUMNS),
        "low_recovery_threshold": LOW_RECOVERY_THRESHOLD,
        "low_recovery_count": int(data.low_recovery.sum()),
        "models": {},
        "warning": "Synthetic engineering checks only; no real-world performance claim or promotion.",
    }
    algorithms: tuple[Algorithm, ...] = ("ridge", "sgd", "gradient_boosting")
    for name in algorithms:
        validation = validate_supervised(data, partial(RecoveryRegressor, name, seed))
        model = RecoveryRegressor(name, seed).fit(data.features, data.target)
        model.residuals = validation.residuals
        version = registry.log_model(
            f"synthetic-recovery-{name}", model, validation, data_kind="synthetic"
        )
        prediction = model.predict(history.iloc[[-1]][list(FEATURE_COLUMNS)])
        interval = bootstrap_prediction_intervals(
            prediction.tolist(), validation.residuals, seed=seed
        )
        report["models"][name] = {
            **validation.aggregate(),
            "version": version,
            "next_prediction": float(prediction[0]),
            "interval_90": interval[0].tolist(),
        }
    classifier_validation = validate_supervised(data, low_recovery_classifier, classifier=True)
    if data.low_recovery.nunique() < 2 or len(classifier_validation.folds) < 2:
        report["models"]["logistic"] = {"status": "insufficient_class_diversity"}
    else:
        classifier = low_recovery_classifier().fit(data.features, data.low_recovery)
        version = registry.log_model(
            "synthetic-low-recovery-logistic",
            classifier,
            classifier_validation,
            data_kind="synthetic",
        )
        report["models"]["logistic"] = {**classifier_validation.aggregate(), "version": version}
    for anomaly_name, factory in (
        ("robust_z", RollingAnomalyDetector),
        ("isolation_forest", IsolationAnomalyDetector),
    ):
        anomaly_validation = validate_anomaly(history, factory)
        detector = factory().fit(history)
        version = registry.log_model(
            f"synthetic-anomaly-{anomaly_name}",
            detector,
            anomaly_validation,
            data_kind="synthetic",
            tags={"horizon": "current_cycle", "interpretation": "deviation_not_diagnosis"},
        )
        report["models"][anomaly_name] = {**anomaly_validation.aggregate(), "version": version}
    (root / "synthetic_results.json").write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".modeling/synthetic"))
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    report = run_synthetic_suite(args.output, days=args.days, seed=args.seed)
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
