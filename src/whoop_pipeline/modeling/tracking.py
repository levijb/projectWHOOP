"""Explicit local SQLite MLflow tracking + model registry; ambient remote URIs are ignored."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import unquote, urlparse

from .validation import ValidationResult


class LocalModelRegistry:
    """Own a local MLflow store; never use a default/global tracking or registry client."""

    def __init__(self, root: Path) -> None:
        if str(root).startswith(("\\\\", "//")) or "://" in str(root):
            raise ValueError("MLflow root must be a local filesystem directory")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        os.environ["MLFLOW_ENABLE_TELEMETRY"] = "false"
        from mlflow import MlflowClient

        uri = f"sqlite:///{(self.root / 'mlflow.db').as_posix()}"
        self.client = MlflowClient(tracking_uri=uri, registry_uri=uri)
        self.artifacts = self.root / "mlruns"

    def log_model(
        self,
        name: str,
        model: Any,
        validation: ValidationResult,
        *,
        data_kind: str,
        tags: dict[str, str] | None = None,
    ) -> str:
        """Version every snapshot in MLflow; only explicit callers choose an active alias."""
        import mlflow.sklearn
        from mlflow.exceptions import MlflowException

        experiment_name = f"whoop-{data_kind}"
        experiment = self.client.get_experiment_by_name(experiment_name)
        experiment_id = (
            experiment.experiment_id
            if experiment
            else self.client.create_experiment(
                experiment_name,
                artifact_location=(self.artifacts / data_kind).as_uri(),
            )
        )
        if experiment:
            self._local_path(experiment.artifact_location)
        run = self.client.create_run(
            experiment_id,
            tags={
                "mlflow.runName": name,
                "data_kind": data_kind,
                "horizon": "next_cycle",
                **(tags or {}),
            },
        )
        run_id = run.info.run_id
        try:
            self._local_path(run.info.artifact_uri)
            for key, value in model.get_params().items():
                self.client.log_param(run_id, key, str(value))
            for key, value in validation.aggregate().items():
                self.client.log_metric(run_id, key, value)
            for step, fold in enumerate(validation.folds):
                for key, value in fold.items():
                    self.client.log_metric(run_id, f"fold_{key}", value, step=step)
            with TemporaryDirectory(prefix="whoop-model-") as directory:
                path = Path(directory) / "model"
                # Explicit requirements avoid MLflow's environment-inference subprocess.
                mlflow.sklearn.save_model(
                    model,
                    str(path),
                    serialization_format="cloudpickle",
                    pip_requirements=[
                        "scikit-learn>=1.6,<2",
                        "numpy>=1.24,<3",
                        "pandas>=2.1,<3",
                        "projectwhoop==0.1.0",
                    ],
                )
                self.client.log_artifacts(run_id, str(path), artifact_path="model")
            try:
                self.client.get_registered_model(name)
            except MlflowException as error:
                if error.error_code != "RESOURCE_DOES_NOT_EXIST":
                    raise
                self.client.create_registered_model(name)
            artifact_uri = self.client.get_run(run_id).info.artifact_uri
            self._local_path(artifact_uri)  # Reject a preexisting experiment pointing remotely.
            version = self.client.create_model_version(
                name,
                source=f"{artifact_uri}/model",
                run_id=run_id,
                tags={"data_kind": data_kind, **(tags or {})},
            )
            self.client.set_terminated(run_id)
            return str(version.version)
        except Exception:
            self.client.set_terminated(run_id, status="FAILED")
            raise

    def _local_path(self, uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != "file" or parsed.netloc:
            raise ValueError("Only local MLflow artifacts are supported")
        path_text = unquote(parsed.path)
        if os.name == "nt" and len(path_text) > 2 and path_text[0] == "/" and path_text[2] == ":":
            path_text = path_text[1:]
        path = Path(path_text).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Artifact path escapes the local tracking directory")
        return path

    def load(self, name: str, version: str) -> Any:
        import mlflow.sklearn

        entry = self.client.get_model_version(name, version)
        if entry.source is None:
            raise ValueError("Registered model has no local artifact source")
        return mlflow.sklearn.load_model(str(self._local_path(entry.source)))

    def active_version(self, name: str) -> str | None:
        from mlflow.exceptions import MlflowException

        try:
            return str(self.client.get_model_version_by_alias(name, "active").version)
        except MlflowException as error:
            if error.error_code not in {"RESOURCE_DOES_NOT_EXIST", "INVALID_PARAMETER_VALUE"}:
                raise
            return None

    def activate(self, name: str, version: str) -> None:
        self.client.set_registered_model_alias(name, "active", version)
