"""Model asset activation and feature retrieval use local substitutes only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest
from dagster import materialize

from whoop_pipeline.modeling.synthetic import synthetic_history
from whoop_pipeline.orchestration.dbt_assets import feature_marts
from whoop_pipeline.orchestration.model_assets import ModelingResource, daily_model_update
from whoop_pipeline.orchestration.resources import LocalBackend
from whoop_pipeline.orchestration.resources import PostgresBackend as StorageResource
from whoop_pipeline.storage.postgres_backend import PostgresBackend
from whoop_pipeline.storage.predictions import PredictionStore, read_postgres_history


def test_modeling_is_disabled_without_both_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake@db.invalid/fake")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://tracking.invalid")
    for enabled, allow in ((False, False), (True, False), (False, True)):
        resource = ModelingResource(
            enabled=enabled, allow_real_training=allow, tracking_dir=str(tmp_path / "untouched")
        )
        assert resource.update(LocalBackend())["status"] == "disabled"
    assert not (tmp_path / "untouched").exists()


def test_modeling_cannot_bypass_postgres_opt_in() -> None:
    resource = ModelingResource(enabled=True, allow_real_training=True)
    with pytest.raises(RuntimeError, match="explicitly enable"):
        resource.update(StorageResource(database_url="postgresql://fake@db.invalid/fake"))


def test_scheduled_job_excludes_model_asset() -> None:
    from whoop_pipeline.orchestration.definitions import defs

    job = defs.resolve_job_def("whoop_pipeline_job")
    assert "daily_model_update" not in job.graph.node_names()
    assert "daily_model_update" in defs.resolve_job_def("whoop_model_update_job").graph.node_names()


def test_enabled_model_asset_reads_sqlite_mart_and_persists_forecast(
    backend: PostgresBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Minimal gold columns referenced by the read query, in the migrated local substitute.
    # SQLAlchemy to_sql appends to existing Alembic tables; use full required records below.
    history = synthetic_history(120)
    offset = pd.Timestamp(datetime.now(UTC)).normalize() - history.iloc[-1]["start_at"]
    history["start_at"] += offset
    history["end_at"] += offset
    with backend.engine.begin() as connection:
        history.drop(
            columns=[
                "end_at",
                "resting_heart_rate",
                "skin_temp_celsius",
                "respiratory_rate",
                "spo2_percentage",
            ]
        ).to_sql("mart_daily_features", connection, index=False)
        cycles = history[["cycle_id", "user_id", "start_at", "end_at"]].copy()
        cycles["created_at"] = cycles["start_at"]
        cycles["updated_at"] = cycles["start_at"]
        cycles["timezone_offset"] = "+00:00"
        cycles["score_state"] = "SCORED"
        cycles.to_sql("cycles", connection, if_exists="append", index=False)
        recovery = history[
            ["cycle_id", "user_id", "resting_heart_rate", "skin_temp_celsius", "spo2_percentage"]
        ].copy()
        recovery["sleep_id"] = history["cycle_id"].astype(str)
        recovery["created_at"] = history["start_at"]
        recovery["updated_at"] = history["start_at"]
        recovery["score_state"] = "SCORED"
        recovery.to_sql("recovery", connection, if_exists="append", index=False)
        sleep = history[["cycle_id", "user_id", "start_at", "end_at", "respiratory_rate"]].copy()
        sleep["sleep_id"] = history["cycle_id"].astype(str)
        sleep["created_at"] = history["start_at"]
        sleep["updated_at"] = history["start_at"]
        sleep["timezone_offset"] = "+00:00"
        sleep["score_state"] = "SCORED"
        sleep["is_nap"] = False
        sleep.to_sql("sleep", connection, if_exists="append", index=False)
    fetched = read_postgres_history(backend)
    assert len(fetched) == 120
    assert fetched["respiratory_rate"].tolist() == history["respiratory_rate"].tolist()
    # The production resource selection gate is exercised, but the injected URL is SQLite.
    url = str(backend.engine.url)
    monkeypatch.setenv("WHOOP_PIPELINE_USE_POSTGRES", "true")
    monkeypatch.setenv("DATABASE_URL", url)
    result = materialize(
        [*feature_marts.to_source_assets(), daily_model_update],
        resources={
            "storage": StorageResource(database_url=url),
            "modeling": ModelingResource(
                enabled=True, allow_real_training=True, tracking_dir=str(tmp_path / "registry")
            ),
        },
    )
    assert result.success
    assert result.output_for_node("daily_model_update")["status"] == "predicted"
    assert len(PredictionStore(backend).records("real-recovery-sgd-daily")) == 1
