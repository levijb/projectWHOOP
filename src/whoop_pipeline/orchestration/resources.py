"""Swappable WHOOP and storage resources; fixture/local defaults need no credentials."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
from dagster import ConfigurableResource, InitResourceContext
from pydantic import PrivateAttr

from whoop_pipeline.client import WhoopClient
from whoop_pipeline.config import WhoopConfig
from whoop_pipeline.oauth import ensure_fresh_token
from whoop_pipeline.storage.bronze import BRONZE_RECORD_TYPES
from whoop_pipeline.storage.bronze import read_sync_state as local_read_sync_state
from whoop_pipeline.storage.bronze import update_sync_state as local_update_sync_state
from whoop_pipeline.storage.duckdb_loader import load_silver_frames
from whoop_pipeline.storage.postgres_backend import PostgresBackend as _PostgresBackend

WhoopRecords = dict[str, list[dict[str, Any]]]


class WhoopDataSource(Protocol):
    """Anything that can fetch the four WHOOP v2 collections for a date window."""

    def fetch_all(self, start: datetime, end: datetime) -> WhoopRecords: ...


class LiveWhoopResource(ConfigurableResource):
    """Use stored/rotating tokens only with a persistent backend.

    Local live pulls retain Phase 2's access-token-only behavior. No implicit dotenv loading:
    the caller (or Dagster CLI) owns loading its environment. A URL is injected only after
    the separate Postgres opt-in. EnvVar references keep credentials out of Dagster config.
    """

    database_url: str | None = None

    def fetch_all(self, start: datetime, end: datetime) -> WhoopRecords:
        store = _PostgresBackend(self.database_url) if self.database_url else None
        try:
            if store is None:
                config = WhoopConfig.from_env(env_file=None)
                access_token = config.require_access_token()
            else:
                config = WhoopConfig.from_env(required=(), env_file=None)
                access_token = ensure_fresh_token(config, token_store=store).access_token
            client = WhoopClient(access_token)
            return {
                "cycles": client.get_all_pages(client.get_cycle_collection, start, end),
                "recovery": client.get_all_pages(client.get_recovery_collection, start, end),
                "sleep": client.get_all_pages(client.get_sleep_collection, start, end),
                "workouts": client.get_all_pages(client.get_workout_collection, start, end),
            }
        finally:
            if store is not None:
                store.close()


class FixtureWhoopResource(ConfigurableResource):
    """Returns Phase 1 fixtures regardless of the requested window. Local dev/test only."""

    fixtures_dir: str = "tests/fixtures"

    def fetch_all(self, start: datetime, end: datetime) -> WhoopRecords:
        directory = Path(self.fixtures_dir)
        return {
            record_type: json.loads((directory / f"{record_type}.json").read_text(encoding="utf-8"))
            for record_type in BRONZE_RECORD_TYPES
        }


class PipelinePathsResource(ConfigurableResource):
    """Local bronze output. In scheduled containers these files are ephemeral."""

    data_dir: str = "data"


class GoldStorageBackend(Protocol):
    """Validated gold and incremental progress; checkpoint only after successful gold writes."""

    def load_gold(
        self,
        cycles_df: pd.DataFrame,
        recovery_df: pd.DataFrame,
        sleep_df: pd.DataFrame,
        workouts_df: pd.DataFrame,
        *,
        last_synced_date: date | None = None,
    ) -> None: ...
    def read_sync_state(self) -> date | None: ...
    def update_sync_state(self, last_synced_date: date) -> None: ...


class LocalBackend(ConfigurableResource):
    """Phase 1 DuckDB + JSON state, with writes ordered for safe replay after a failure."""

    data_dir: str = "data"
    database_path: str | None = None

    @property
    def resolved_database_path(self) -> Path:
        return Path(self.database_path or str(Path(self.data_dir) / "processed" / "whoop.db"))

    def load_gold(
        self,
        cycles_df: pd.DataFrame,
        recovery_df: pd.DataFrame,
        sleep_df: pd.DataFrame,
        workouts_df: pd.DataFrame,
        *,
        last_synced_date: date | None = None,
    ) -> None:
        load_silver_frames(
            cycles_df,
            recovery_df,
            sleep_df,
            workouts_df,
            database_path=self.resolved_database_path,
        )
        if last_synced_date is not None:
            self.update_sync_state(last_synced_date)

    def read_sync_state(self) -> date | None:
        return local_read_sync_state(data_dir=self.data_dir)

    def update_sync_state(self, last_synced_date: date) -> None:
        local_update_sync_state(last_synced_date, data_dir=self.data_dir)


class PostgresBackend(ConfigurableResource):
    """Dagster wrapper. Schema is provisioned separately with Alembic, never by ingestion."""

    database_url: str
    _backend: _PostgresBackend | None = PrivateAttr(default=None)

    def _get_backend(self) -> _PostgresBackend:
        if self._backend is None:
            self._backend = _PostgresBackend(self.database_url)
        return self._backend

    def teardown_after_execution(self, context: InitResourceContext) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None

    def load_gold(
        self,
        cycles_df: pd.DataFrame,
        recovery_df: pd.DataFrame,
        sleep_df: pd.DataFrame,
        workouts_df: pd.DataFrame,
        *,
        last_synced_date: date | None = None,
    ) -> None:
        self._get_backend().load_gold(
            cycles_df, recovery_df, sleep_df, workouts_df, last_synced_date=last_synced_date
        )

    def read_sync_state(self) -> date | None:
        return self._get_backend().read_sync_state()

    def update_sync_state(self, last_synced_date: date) -> None:
        self._get_backend().update_sync_state(last_synced_date)
