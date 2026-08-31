"""Real Alembic migration and serving operations on the established SQLite substitute."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import event

from whoop_pipeline.modeling.data import FEATURE_COLUMNS
from whoop_pipeline.modeling.service import update_daily_model
from whoop_pipeline.modeling.synthetic import synthetic_history
from whoop_pipeline.modeling.tracking import LocalModelRegistry
from whoop_pipeline.storage.postgres_backend import PostgresBackend
from whoop_pipeline.storage.predictions import Prediction, PredictionStore


def test_forecast_retry_preserves_original_and_settlement_uses_next_cycle(
    backend: PostgresBackend,
) -> None:
    store = PredictionStore(backend)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    forecast = Prediction(10, "test-sgd", "1", start, start + timedelta(hours=12), 60, 45, 75)
    assert store.save(forecast)
    assert not store.save(replace(forecast, model_version="2", predicted_value=90))
    history = pd.DataFrame(
        {
            "cycle_id": [10, 300, 900],
            "start_at": pd.date_range(start, periods=3),
            "recovery_score": [95.0, None, 40.0],
        }
    )
    store.settle(history, "test-sgd")
    row = store.records("test-sgd")[0]
    assert row["target_cycle_id"] == 300 and row["actual_value"] is None
    history.loc[1, "recovery_score"] = 50
    store.settle(history, "test-sgd")
    store.settle(history, "test-sgd")
    row = store.records("test-sgd")[0]
    assert row["predicted_value"] == 60 and row["model_version"] == "1"
    assert row["actual_value"] == 50 and row["error"] == 10


def test_settlement_rolls_back_when_write_fails(backend: PostgresBackend) -> None:
    store = PredictionStore(backend)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    store.save(Prediction(10, "test", "1", start, start, 60, 45, 75))
    history = pd.DataFrame(
        {
            "cycle_id": [10, 11],
            "start_at": [start, start + timedelta(days=1)],
            "recovery_score": [80.0, 50.0],
        }
    )

    def fail(*args: object) -> None:
        if str(args[2]).startswith("UPDATE"):
            raise RuntimeError("simulated serving failure")

    event.listen(backend.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            store.settle(history, "test")
    finally:
        event.remove(backend.engine, "before_cursor_execute", fail)
    assert store.records("test")[0]["actual_value"] is None


def test_daily_initial_update_replay_routine_partial_fit_and_restart(
    backend: PostgresBackend, tmp_path: Path
) -> None:
    history = synthetic_history(180)
    store = PredictionStore(backend)
    registry = LocalModelRegistry(tmp_path / "registry")

    def invoke(count: int) -> dict:
        return update_daily_model(
            history.iloc[:count],
            store,
            registry,
            now=(history.iloc[count - 1]["start_at"] + pd.Timedelta(hours=12)).to_pydatetime(),
            data_kind="synthetic",
        )

    first = invoke(120)
    assert first["status"] == "predicted" and first["reason"] == "initial_training"
    name = "synthetic-recovery-sgd-daily"
    initial = registry.load(name, first["model_version"])
    assert invoke(120)["status"] == "already_predicted"
    assert len(registry.client.search_model_versions(f"name = '{name}'")) == 1
    # No forecasts were issued on intervening days, so the next decision is routine.
    next_run = invoke(127)
    assert next_run["status"] == "predicted" and next_run["reason"] == "routine"
    updated = registry.load(name, next_run["model_version"])
    assert updated.estimator.t_ > initial.estimator.t_
    assert pd.Timestamp(updated.metadata["trained_through"]) == history.iloc[126]["start_at"]
    pd.testing.assert_series_equal(
        pd.Series(initial.preprocessor.named_steps["scaler"].mean_),
        pd.Series(updated.preprocessor.named_steps["scaler"].mean_),
    )
    entry = registry.client.get_model_version(name, next_run["model_version"])
    assert entry.tags["update_method"] == "partial_fit"
    assert store.records(name)[0]["target_cycle_id"] == int(history.iloc[120]["cycle_id"])
    assert store.records(name)[0]["actual_value"] == pytest.approx(
        history.iloc[120]["recovery_score"]
    )
    reopened = LocalModelRegistry(tmp_path / "registry")
    assert reopened.active_version(name) == next_run["model_version"]
    assert reopened.load(name, next_run["model_version"]).predict(
        history.iloc[[-1]][list(FEATURE_COLUMNS)]
    ).shape == (1,)


def test_insufficient_or_unapproved_real_history_does_not_train(
    backend: PostgresBackend, tmp_path: Path
) -> None:
    history = synthetic_history()
    store, registry = PredictionStore(backend), LocalModelRegistry(tmp_path / "registry")
    now = (history.iloc[-1]["start_at"] + pd.Timedelta(hours=12)).to_pydatetime()
    assert (
        update_daily_model(history, store, registry, now=now, data_kind="real")["status"]
        == "disabled"
    )
    assert (
        update_daily_model(history.tail(2), store, registry, now=now, data_kind="synthetic")[
            "reason"
        ]
        == "insufficient_history"
    )
    assert not registry.client.search_registered_models()


def test_failed_forecast_insert_reuses_activated_snapshot(
    backend: PostgresBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = synthetic_history(120)
    registry = LocalModelRegistry(tmp_path / "registry")
    store = PredictionStore(backend)
    now = (history.iloc[-1]["start_at"] + pd.Timedelta(hours=12)).to_pydatetime()
    original_save = store.save

    def failed_save(prediction: Prediction) -> bool:
        raise RuntimeError("simulated forecast insert failure")

    monkeypatch.setattr(store, "save", failed_save)
    with pytest.raises(RuntimeError, match="forecast insert"):
        update_daily_model(history, store, registry, now=now, data_kind="synthetic")
    name = "synthetic-recovery-sgd-daily"
    assert registry.active_version(name) == "1"
    assert not store.records(name)
    monkeypatch.setattr(store, "save", original_save)
    result = update_daily_model(history, store, registry, now=now, data_kind="synthetic")
    assert result["status"] == "predicted" and result["updated"] is False
    assert result["model_version"] == "1"
    assert len(registry.client.search_model_versions(f"name = '{name}'")) == 1
