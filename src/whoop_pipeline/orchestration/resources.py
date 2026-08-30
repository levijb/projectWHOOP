"""Swappable WHOOP data sources for the ``raw_whoop_data`` asset.

``LiveWhoopResource`` wraps the tested :class:`WhoopClient` for real pulls (unused this
session -- no live credentials are configured or exercised). ``FixtureWhoopResource`` returns
Phase 1's static JSON fixtures regardless of the requested window, so the full asset graph can
be materialized locally and in CI with zero credentials.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from dagster import ConfigurableResource

from whoop_pipeline.client import WhoopClient
from whoop_pipeline.config import WhoopConfig
from whoop_pipeline.storage.bronze import BRONZE_RECORD_TYPES

WhoopRecords = dict[str, list[dict[str, Any]]]


class WhoopDataSource(Protocol):
    """Anything that can fetch the four WHOOP v2 collections for a date window."""

    def fetch_all(self, start: datetime, end: datetime) -> WhoopRecords: ...


class LiveWhoopResource(ConfigurableResource):
    """Fetches real WHOOP v2 data using the tested client. Requires WHOOP_ACCESS_TOKEN."""

    def fetch_all(self, start: datetime, end: datetime) -> WhoopRecords:
        config = WhoopConfig.from_env(required=("WHOOP_ACCESS_TOKEN",))
        client = WhoopClient(config.require_access_token())
        return {
            "cycles": client.get_all_pages(client.get_cycle_collection, start, end),
            "recovery": client.get_all_pages(client.get_recovery_collection, start, end),
            "sleep": client.get_all_pages(client.get_sleep_collection, start, end),
            "workouts": client.get_all_pages(client.get_workout_collection, start, end),
        }


class FixtureWhoopResource(ConfigurableResource):
    """Returns the Phase 1 test fixtures, ignoring the requested window. Local dev/test only."""

    fixtures_dir: str = "tests/fixtures"

    def fetch_all(self, start: datetime, end: datetime) -> WhoopRecords:
        directory = Path(self.fixtures_dir)
        return {
            record_type: json.loads((directory / f"{record_type}.json").read_text(encoding="utf-8"))
            for record_type in BRONZE_RECORD_TYPES
        }


class PipelinePathsResource(ConfigurableResource):
    """Where bronze/state/gold land. Defaults match production; tests override both to a
    tmp_path so materializing the asset graph never touches the real data/ directory."""

    data_dir: str = "data"
    database_path: str = "data/processed/whoop.db"
