"""Single source of truth for both ``dagster dev`` and ``dagster job execute``.

The WHOOP data source resource defaults to the fixture-backed fake unless
``WHOOP_PIPELINE_USE_LIVE_CLIENT`` is explicitly set to a truthy value. This is a deliberate,
separate opt-in flag -- it is NOT gated on whether ``WHOOP_ACCESS_TOKEN`` happens to be present,
because Dagster's CLI auto-loads a ``.env`` file from the working directory, and this repo has
a real one (for unrelated manual/notebook use) containing a real access token. Gating on token
presence alone caused a live call to api.prod.whoop.com to be attempted from a plain
`dagster job execute` run in this directory during development of this module; it only failed
because of an unrelated local SSL/network issue, not because of any safeguard here. Requiring
a separate, explicit flag makes that failure mode impossible instead of merely unlikely.
"""

from __future__ import annotations

import os

from dagster import Definitions, define_asset_job
from dagster_dbt import DbtCliResource

from .assets import bronze_partitions, gold_tables, raw_whoop_data, silver_frames
from .dbt_assets import DBT_PROJECT_DIR, feature_marts
from .resources import (
    FixtureWhoopResource,
    LiveWhoopResource,
    PipelinePathsResource,
    WhoopDataSource,
)

ALL_ASSETS = [raw_whoop_data, bronze_partitions, silver_frames, gold_tables, feature_marts]

whoop_pipeline_job = define_asset_job(name="whoop_pipeline_job", selection=ALL_ASSETS)


def _default_whoop_resource() -> WhoopDataSource:
    if os.environ.get("WHOOP_PIPELINE_USE_LIVE_CLIENT", "").lower() in ("1", "true", "yes"):
        return LiveWhoopResource()
    return FixtureWhoopResource()


defs = Definitions(
    assets=ALL_ASSETS,
    jobs=[whoop_pipeline_job],
    resources={
        "whoop": _default_whoop_resource(),
        "paths": PipelinePathsResource(),
        "dbt": DbtCliResource(project_dir=str(DBT_PROJECT_DIR)),
    },
)
