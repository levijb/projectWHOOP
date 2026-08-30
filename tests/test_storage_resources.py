from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from dagster import EnvVar, materialize
from sqlalchemy import create_engine

from whoop_pipeline.orchestration import definitions
from whoop_pipeline.orchestration.assets import (
    bronze_partitions,
    gold_tables,
    raw_whoop_data,
    silver_frames,
)
from whoop_pipeline.orchestration.resources import (
    FixtureWhoopResource,
    LiveWhoopResource,
    LocalBackend,
    PipelinePathsResource,
)
from whoop_pipeline.orchestration.resources import (
    PostgresBackend as PostgresResource,
)
from whoop_pipeline.storage.database import dbt_postgres_environment
from whoop_pipeline.storage.postgres_backend import PostgresBackend


@pytest.mark.parametrize(
    "live,postgres", [(False, False), (True, False), (False, True), (True, True)]
)
def test_flags_select_matching_resources_and_dbt_target_without_connecting(
    monkeypatch: pytest.MonkeyPatch, live: bool, postgres: bool
) -> None:
    url = "postgresql://offline:secret-marker@db.invalid/whoop"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("WHOOP_ACCESS_TOKEN", "offline-marker")
    monkeypatch.setenv("WHOOP_PIPELINE_USE_LIVE_CLIENT", str(live))
    monkeypatch.setenv("WHOOP_PIPELINE_USE_POSTGRES", str(postgres))
    for key in dbt_postgres_environment(url):
        monkeypatch.setenv(key, "")
    source = definitions._default_whoop_resource()
    storage = definitions._default_storage_resource()
    dbt = definitions._default_dbt_resource()
    assert isinstance(source, LiveWhoopResource if live else FixtureWhoopResource)
    assert isinstance(storage, PostgresResource if postgres else LocalBackend)
    assert dbt.target == ("prod" if postgres else "dev")
    if postgres:
        assert storage.database_url == EnvVar("DATABASE_URL")
        assert "secret-marker" not in str(storage._convert_to_config_dictionary())
    if live:
        assert source.database_url == (EnvVar("DATABASE_URL") if postgres else None)


def test_postgres_opt_in_without_url_fails_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WHOOP_PIPELINE_USE_POSTGRES", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not"):
        definitions._default_storage_resource()


@pytest.mark.parametrize("kind", ["local", "postgres"])
@pytest.mark.parametrize("invalid", [False, True])
def test_python_asset_graph_uses_backend_checkpoint_and_does_not_advance_on_failure(
    tmp_path: Path,
    backend: PostgresBackend,
    fixture_records: dict[str, list[dict[str, Any]]],
    kind: str,
    invalid: bool,
) -> None:
    prior = date(2026, 1, 1)
    storage = (
        LocalBackend(data_dir=str(tmp_path / "local"))
        if kind == "local"
        else PostgresResource(database_url=str(backend.engine.url))
    )
    storage.update_sync_state(prior)
    if invalid:
        fixture_records["recovery"][0]["score"]["recovery_score"] = 999
    windows: list[tuple[datetime, datetime]] = []

    class Source:
        def fetch_all(self, start: datetime, end: datetime) -> dict[str, list[dict[str, Any]]]:
            windows.append((start, end))
            return fixture_records

    result = materialize(
        [raw_whoop_data, bronze_partitions, silver_frames, gold_tables],
        resources={
            "whoop": Source(),
            "paths": PipelinePathsResource(data_dir=str(tmp_path / "bronze")),
            "storage": storage,
        },
        raise_on_error=False,
    )
    assert result.success is not invalid
    assert windows[0][0] == datetime(2026, 1, 1, tzinfo=UTC)
    assert storage.read_sync_state() == (prior if invalid else windows[0][1].date())


def test_postgres_engine_hides_parameters_and_does_not_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_engine(url: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        captured["url"] = url
        return create_engine("sqlite://")

    monkeypatch.setattr("whoop_pipeline.storage.postgres_backend.create_engine", fake_engine)
    backend = PostgresBackend("postgresql://fake:marker@db.invalid/whoop")
    assert backend.engine is not None
    assert captured["hide_parameters"] is True
    assert captured["url"].query["sslmode"] == "require"
    backend.close()
