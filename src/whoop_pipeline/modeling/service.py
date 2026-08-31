"""Deliberately invoked daily recovery updates; no network/resource selection in this module."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast

import pandas as pd

from whoop_pipeline.storage.predictions import Prediction, PredictionStore

from .data import FEATURE_COLUMNS, ordered_history, require_history, supervised_data
from .models import RecoveryRegressor
from .monitoring import bootstrap_prediction_intervals, retrain_decision
from .tracking import LocalModelRegistry
from .validation import validate_supervised


def update_daily_model(
    history: pd.DataFrame,
    store: PredictionStore,
    registry: LocalModelRegistry,
    *,
    now: datetime,
    data_kind: Literal["synthetic", "real"],
    allow_real_training: bool = False,
) -> dict[str, Any]:
    """Settle outcomes, decide, update SGD, version locally, then persist the next forecast.

    Invoke serially against one persistent registry/store. The watermark lives in the MLflow
    model snapshot. Activating before saving allows a failed SQL write to retry without a
    second partial_fit. A crash before activation may leave an unused registry version.
    """
    if data_kind == "real" and not allow_real_training:
        return {"status": "disabled", "reason": "real_training_requires_deliberate_opt_in"}
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    history = ordered_history(history)
    if history.empty:
        return {"status": "skipped", "reason": "insufficient_history"}
    latest = history.iloc[-1]
    elapsed = pd.Timestamp(now) - latest["start_at"]
    if elapsed < pd.Timedelta(0) or elapsed > pd.Timedelta(days=2):
        return {"status": "skipped", "reason": "stale_or_future_features"}
    data = supervised_data(history)
    try:
        require_history(data)
    except ValueError:
        return {
            "status": "skipped",
            "reason": "insufficient_history",
            "labeled_pairs": len(data.target),
        }
    name = f"{data_kind}-recovery-sgd-daily"
    store.settle(history, name)
    records = store.records(name)
    if any(row["cycle_id"] == int(latest["cycle_id"]) for row in records):
        return {"status": "already_predicted", "cycle_id": int(latest["cycle_id"])}
    version = registry.active_version(name)
    model: RecoveryRegressor
    if version is None:
        reason = "initial_training"
        should_update = True
        model = RecoveryRegressor()
    else:
        model = cast(RecoveryRegressor, registry.load(name, version))
        if model.metadata.get("data_kind") != data_kind:
            raise ValueError("Cannot mix synthetic and real model state")
        if model.metadata.get("user_id") != str(int(latest["user_id"])):
            raise ValueError("Active model belongs to another user")
        errors = [float(row["error"]) for row in records if row["error"] is not None]
        days = max(0, (now - datetime.fromisoformat(model.metadata["updated_at"])).days)
        should_update, reason = retrain_decision(errors[-14:], days_since_update=days)
        if data.origins["target_start_at"].max() <= pd.Timestamp(model.metadata["trained_through"]):
            should_update, reason = False, "no_new_labels"
    if should_update:
        # Comparable cold-fit walk-forward reference; not an in-sample snapshot score.
        validation = validate_supervised(data, RecoveryRegressor)
        if version is None or reason == "emergency":
            model = RecoveryRegressor().fit(data.features, data.target)
            update_method = "full_fit"
        else:
            new = data.origins["target_start_at"] > pd.Timestamp(model.metadata["trained_through"])
            model.partial_fit(data.features.loc[new], data.target.loc[new])
            update_method = "partial_fit"
        model.residuals = validation.residuals
        model.metadata = {
            "trained_through": data.origins["target_start_at"].max().isoformat(),
            "updated_at": now.isoformat(),
            "data_kind": data_kind,
            "user_id": str(int(latest["user_id"])),
        }
        version = registry.log_model(
            name,
            model,
            validation,
            data_kind=data_kind,
            tags={
                **model.metadata,
                "trigger_reason": reason,
                "update_method": update_method,
                "evaluation": "fresh_fit_walk_forward_reference",
                "features": ",".join(FEATURE_COLUMNS),
            },
        )
        registry.activate(name, version)
    assert version is not None
    point = float(model.predict(history.iloc[[-1]][list(FEATURE_COLUMNS)])[0])
    lower, upper = bootstrap_prediction_intervals([point], model.residuals)[0]
    prediction = Prediction(
        cycle_id=int(latest["cycle_id"]),
        model_name=name,
        model_version=version,
        origin_at=latest["start_at"].to_pydatetime(),
        created_at=now,
        predicted_value=point,
        ci_lower=float(lower),
        ci_upper=float(upper),
    )
    saved = store.save(prediction)
    return {
        "status": "predicted" if saved else "already_predicted",
        "reason": reason,
        "updated": should_update,
        "cycle_id": prediction.cycle_id,
        "model_version": version,
        "prediction": point,
        "ci_lower": float(lower),
        "ci_upper": float(upper),
    }
