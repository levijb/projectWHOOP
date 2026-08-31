"""Single source of truth for both ``dagster dev`` and ``dagster job execute``.

Both the WHOOP data source and the gold storage backend default to their safe, local,
zero-credential form unless a **separate, explicit** opt-in env var is set --
``WHOOP_PIPELINE_USE_LIVE_CLIENT`` for the real WHOOP client, ``WHOOP_PIPELINE_USE_POSTGRES``
for Postgres. Neither is ever inferred from whether a credential/URL merely happens to be
present in the environment. This is deliberate, not stylistic: Dagster's CLI auto-loads a
``.env`` file from the working directory, and this repo has a real one (for unrelated
manual/notebook use) containing a real access token and, as of Phase 3, a real DATABASE_URL.
Gating on token presence alone caused a live call to api.prod.whoop.com to be attempted from a
plain `dagster job execute` run in this directory during Phase 2's development; it only failed
because of an unrelated local SSL/network issue, not because of any safeguard here. The same
principle now applies to Postgres: a real DATABASE_URL sitting in .env for the human's own
reference must never cause a real connection attempt on its own.
"""

from __future__ import annotations

import os

from dagster import Definitions, EnvVar, define_asset_job
from dagster_dbt import DbtCliResource

from whoop_pipeline.storage.database import (
    dbt_postgres_environment,
    is_enabled,
    require_postgres_opt_in,
)

from .assets import bronze_partitions, gold_tables, raw_whoop_data, silver_frames
from .dbt_assets import DBT_PROJECT_DIR, feature_marts
from .model_assets import ModelingResource, daily_model_update
from .resources import (
    FixtureWhoopResource,
    GoldStorageBackend,
    LiveWhoopResource,
    LocalBackend,
    PipelinePathsResource,
    PostgresBackend,
    WhoopDataSource,
)

ALL_ASSETS = [raw_whoop_data, bronze_partitions, silver_frames, gold_tables, feature_marts]

whoop_pipeline_job = define_asset_job(name="whoop_pipeline_job", selection=ALL_ASSETS)
# Deliberately separate: the scheduled ingestion job's selection and workflow stay unchanged.
whoop_model_update_job = define_asset_job(
    name="whoop_model_update_job", selection=[daily_model_update]
)


def _default_storage_resource() -> GoldStorageBackend:
    if not is_enabled("WHOOP_PIPELINE_USE_POSTGRES"):
        return LocalBackend(
            data_dir=os.environ.get("WHOOP_PIPELINE_DATA_DIR", "data"),
            database_path=os.environ.get("WHOOP_DUCKDB_PATH"),
        )
    require_postgres_opt_in()
    return PostgresBackend(database_url=EnvVar("DATABASE_URL"))


def _default_whoop_resource() -> WhoopDataSource:
    if not is_enabled("WHOOP_PIPELINE_USE_LIVE_CLIENT"):
        return FixtureWhoopResource()
    # Token persistence rides on the same Postgres opt-in as gold storage -- there's no
    # separate flag for it, since "is there a persistent SQL store available" is exactly what
    # WHOOP_PIPELINE_USE_POSTGRES + DATABASE_URL already answers.
    database_url = EnvVar("DATABASE_URL") if is_enabled("WHOOP_PIPELINE_USE_POSTGRES") else None
    return LiveWhoopResource(database_url=database_url)


def _default_dbt_resource() -> DbtCliResource:
    use_postgres = is_enabled("WHOOP_PIPELINE_USE_POSTGRES")
    if use_postgres:
        os.environ.update(dbt_postgres_environment(require_postgres_opt_in()))
    return DbtCliResource(
        project_dir=str(DBT_PROJECT_DIR),
        profiles_dir=str(DBT_PROJECT_DIR),
        target="prod" if use_postgres else "dev",
    )


defs = Definitions(
    assets=[*ALL_ASSETS, daily_model_update],
    jobs=[whoop_pipeline_job, whoop_model_update_job],
    resources={
        "whoop": _default_whoop_resource(),
        "paths": PipelinePathsResource(data_dir=os.environ.get("WHOOP_PIPELINE_DATA_DIR", "data")),
        "storage": _default_storage_resource(),
        "dbt": _default_dbt_resource(),
        "modeling": ModelingResource(),
    },
)
