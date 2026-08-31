"""Opt-in modeling asset, excluded from the existing scheduled ingestion job.

Imports of MLflow/scikit-learn and database reads are delayed until explicit activation.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dagster import AssetExecutionContext, ConfigurableResource, ResourceParam, asset

from whoop_pipeline.storage.database import require_postgres_opt_in

from .dbt_assets import feature_marts
from .resources import GoldStorageBackend, LocalBackend, PostgresBackend


class ModelingResource(ConfigurableResource):  # type: ignore[type-arg]
    enabled: bool = False
    allow_real_training: bool = False
    tracking_dir: str = ".modeling/real"

    def update(self, storage: GoldStorageBackend) -> dict[str, Any]:
        # No filesystem/database/model side effects before BOTH explicit config switches.
        if not self.enabled or not self.allow_real_training:
            return {"status": "disabled", "reason": "manual_activation_required"}
        from filelock import FileLock

        from whoop_pipeline.modeling.data import ordered_history, require_history, supervised_data
        from whoop_pipeline.modeling.service import update_daily_model
        from whoop_pipeline.modeling.tracking import LocalModelRegistry
        from whoop_pipeline.storage.postgres_backend import PostgresBackend as SQLBackend
        from whoop_pipeline.storage.predictions import (
            PredictionStore,
            model_history_sql,
            read_postgres_history,
        )

        if isinstance(storage, PostgresBackend):
            url = require_postgres_opt_in()
            if url != storage.database_url:
                raise ValueError("Model storage must use the explicitly opted-in DATABASE_URL")
            backend = SQLBackend(url)
        elif isinstance(storage, LocalBackend):
            # A caller must migrate this isolated local serving DB first; no runtime DDL.
            backend = SQLBackend(
                f"sqlite:///{Path(self.tracking_dir).resolve() / 'predictions.db'}"
            )
        else:
            raise TypeError("Unsupported modeling storage resource")
        try:
            if isinstance(storage, LocalBackend):
                import duckdb

                with duckdb.connect(
                    str(storage.resolved_database_path), read_only=True
                ) as connection:
                    history = connection.execute(model_history_sql(postgres=False)).fetchdf()
            else:
                history = read_postgres_history(backend)
            history = ordered_history(history)
            try:
                require_history(supervised_data(history))
            except ValueError:
                return {"status": "skipped", "reason": "insufficient_history"}
            registry = LocalModelRegistry(Path(self.tracking_dir))
            with FileLock(str(registry.root / "update.lock"), timeout=0):
                return update_daily_model(
                    history,
                    PredictionStore(backend),
                    registry,
                    now=datetime.now(UTC),
                    data_kind="real",
                    allow_real_training=True,
                )
        finally:
            backend.close()


@asset(deps=[feature_marts], group_name="modeling")
def daily_model_update(
    context: AssetExecutionContext,
    storage: ResourceParam[GoldStorageBackend],
    modeling: ModelingResource,
) -> dict[str, Any]:
    result = modeling.update(storage)
    context.add_output_metadata(
        {key: value for key, value in result.items() if isinstance(value, (str, int, float, bool))}
    )
    return result
