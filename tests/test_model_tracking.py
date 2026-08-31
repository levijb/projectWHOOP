"""Local MLflow experiments and registry versions; no hosted tracking or activation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from whoop_pipeline.modeling.data import FEATURE_COLUMNS
from whoop_pipeline.modeling.demo import run_synthetic_suite
from whoop_pipeline.modeling.synthetic import synthetic_history
from whoop_pipeline.modeling.tracking import LocalModelRegistry


def test_synthetic_suite_logs_six_models_to_local_mlflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://tracking.invalid")
    monkeypatch.setenv("MLFLOW_REGISTRY_URI", "https://registry.invalid")
    report = run_synthetic_suite(tmp_path)
    assert report["data_kind"] == "synthetic"
    assert set(report["models"]) == {
        "ridge",
        "sgd",
        "gradient_boosting",
        "logistic",
        "robust_z",
        "isolation_forest",
    }
    registry = LocalModelRegistry(tmp_path)
    registered = registry.client.search_registered_models()
    assert len(registered) == 6
    assert all(not entry.aliases for entry in registered)
    loaded = registry.load("synthetic-recovery-sgd", report["models"]["sgd"]["version"])
    features = synthetic_history().iloc[[-1]][list(FEATURE_COLUMNS)]
    assert loaded.predict(features)[0] == pytest.approx(report["models"]["sgd"]["next_prediction"])
    entry = registry.client.get_model_version("synthetic-recovery-sgd", "1")
    run = registry.client.get_run(entry.run_id)
    assert run.data.tags["data_kind"] == "synthetic"
    assert {"mae_mean", "mape_mean", "r2_mean"} <= set(run.data.metrics)
    assert run.data.params["algorithm"] == "sgd"
    assert np.isfinite(list(run.data.metrics.values())).all()
    assert (tmp_path / "mlflow.db").exists()
    assert (tmp_path / "synthetic_results.json").exists()


def test_registry_rejects_remote_or_escaping_artifact_stores(tmp_path: Path) -> None:
    registry = LocalModelRegistry(tmp_path)
    for uri in (
        "https://example.invalid/model",
        "s3://bucket/model",
        "file://remote/share/model",
        tmp_path.parent.as_uri(),
    ):
        with pytest.raises(ValueError):
            registry._local_path(uri)
