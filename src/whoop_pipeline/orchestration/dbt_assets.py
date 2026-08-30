"""dbt feature marts as first-class Dagster assets (not a hidden subprocess call).

``WHOOP_DBT_PROJECT_DIR`` lets the Docker image point at wherever it copied the dbt/ directory
(it isn't packaged into the wheel -- see the Dockerfile); it defaults to the dbt/ directory
alongside this checkout for local dev, where the package is installed editable.

No ``from __future__ import annotations`` here: see assets.py for why.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from dagster import AssetExecutionContext, ResourceParam
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

from .resources import GoldStorageBackend, LocalBackend

REPO_ROOT_DBT_DIR = Path(__file__).resolve().parents[3] / "dbt"
DBT_PROJECT_DIR = Path(os.environ.get("WHOOP_DBT_PROJECT_DIR", str(REPO_ROOT_DBT_DIR)))

whoop_dbt_project = DbtProject(project_dir=DBT_PROJECT_DIR, target="dev")
whoop_dbt_project.prepare_if_dev()
# prepare_if_dev() only regenerates the manifest under `dagster dev`. Outside that (pytest,
# `dagster job execute`, a fresh checkout), fall back to generating it once if it's missing --
# the Docker image pre-generates it at build time, so this is a no-op there.
if not whoop_dbt_project.manifest_path.exists():
    whoop_dbt_project.preparer.prepare(whoop_dbt_project)


@dbt_assets(manifest=whoop_dbt_project.manifest_path)
def feature_marts(
    context: AssetExecutionContext,
    dbt: DbtCliResource,
    storage: ResourceParam[GoldStorageBackend],
) -> Iterator[Any]:
    # Keep dbt on the exact DuckDB file the injected local backend just wrote, even if a
    # user changes resource paths in the Dagster UI. Restore the process environment after.
    previous = os.environ.get("WHOOP_DUCKDB_PATH")
    if isinstance(storage, LocalBackend):
        os.environ["WHOOP_DUCKDB_PATH"] = str(storage.resolved_database_path.resolve())
    try:
        yield from dbt.cli(["build"], context=context).stream()
    finally:
        if previous is None:
            os.environ.pop("WHOOP_DUCKDB_PATH", None)
        else:
            os.environ["WHOOP_DUCKDB_PATH"] = previous


__all__ = ["DBT_PROJECT_DIR", "feature_marts", "whoop_dbt_project"]
