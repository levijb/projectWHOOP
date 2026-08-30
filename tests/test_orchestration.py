"""Materializes the whole Dagster asset graph in-process against fixtures.

This is the session's core check: a human running `dagster dev` with zero credentials
configured should see raw_whoop_data -> bronze_partitions -> silver_frames -> gold_tables ->
mart_daily_features complete successfully. All paths are redirected to tmp_path so this never
touches the real data/ directory or data/processed/whoop.db.
"""

from __future__ import annotations

from pathlib import Path

from dagster import materialize
from dagster_dbt import DbtCliResource

from whoop_pipeline.orchestration.assets import (
    bronze_partitions,
    gold_tables,
    raw_whoop_data,
    silver_frames,
)
from whoop_pipeline.orchestration.dbt_assets import DBT_PROJECT_DIR, feature_marts
from whoop_pipeline.orchestration.resources import FixtureWhoopResource, PipelinePathsResource

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_full_asset_graph_materializes_with_fixture_resource_and_zero_credentials(
    tmp_path: Path, monkeypatch: object
) -> None:
    data_dir = tmp_path / "data"
    database_path = data_dir / "processed" / "whoop.db"
    monkeypatch.setenv("WHOOP_DUCKDB_PATH", str(database_path))  # type: ignore[attr-defined]

    result = materialize(
        [raw_whoop_data, bronze_partitions, silver_frames, gold_tables, feature_marts],
        resources={
            "whoop": FixtureWhoopResource(fixtures_dir=str(FIXTURES_DIR)),
            "paths": PipelinePathsResource(
                data_dir=str(data_dir), database_path=str(database_path)
            ),
            "dbt": DbtCliResource(project_dir=str(DBT_PROJECT_DIR)),
        },
    )

    assert result.success
    assert database_path.exists()


def test_definitions_default_to_fixture_resource_without_explicit_opt_in(
    monkeypatch: object,
) -> None:
    """Regression test: the live client must never be picked by ambient credentials alone.

    Dagster's CLI auto-loads a `.env` file from the working directory, and this repo has a
    real one (for unrelated manual/notebook use) with a real WHOOP_ACCESS_TOKEN. Gating the
    live/fixture choice on token presence caused a real call to api.prod.whoop.com to be
    attempted from a plain `dagster job execute` run during development of this module. The
    fix requires a separate, explicit WHOOP_PIPELINE_USE_LIVE_CLIENT flag; this test pins
    that behavior so it can't silently regress.
    """
    monkeypatch.delenv("WHOOP_PIPELINE_USE_LIVE_CLIENT", raising=False)  # type: ignore[attr-defined]
    monkeypatch.setenv("WHOOP_ACCESS_TOKEN", "not-a-real-token-just-checking-the-gate")  # type: ignore[attr-defined]

    import importlib

    from whoop_pipeline.orchestration import definitions

    importlib.reload(definitions)
    try:
        assert isinstance(definitions.defs.resources["whoop"], FixtureWhoopResource)
    finally:
        importlib.reload(definitions)
