"""Software-defined assets wrapping the already-tested Phase 1 ingestion/transform/load code.

Each asset is a thin wrapper: all real logic (retries, atomic writes, flattening, Pandera
validation, idempotent upserts) lives in the Phase 1 modules and is exercised by Phase 1's own
test suite. This module only wires them into a dependency graph.

No ``from __future__ import annotations`` here: Dagster's op/asset decorators need a real
``AssetExecutionContext`` object on the ``context`` parameter, not a stringified forward
reference, or asset creation raises DagsterInvalidDefinitionError.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pandas as pd
from dagster import AssetExecutionContext, MetadataValue, ResourceParam, asset

from whoop_pipeline.ingestion import determine_sync_start
from whoop_pipeline.storage.bronze import read_sync_state, update_sync_state, write_bronze_pull
from whoop_pipeline.storage.duckdb_loader import load_silver_frames
from whoop_pipeline.transform.flatten import (
    flatten_cycles,
    flatten_recovery,
    flatten_sleep,
    flatten_workouts,
)

from .resources import PipelinePathsResource, WhoopDataSource, WhoopRecords


@dataclass(frozen=True, slots=True)
class RawWhoopPull:
    """Raw records for one pull, plus the date they should be attributed to in bronze."""

    pull_date: date
    records: WhoopRecords


@dataclass(frozen=True, slots=True)
class SilverFrames:
    """The four typed, Pandera-validatable DataFrames Phase 1's flatten step produces."""

    cycles: pd.DataFrame
    recovery: pd.DataFrame
    sleep: pd.DataFrame
    workouts: pd.DataFrame


@asset
def raw_whoop_data(
    context: AssetExecutionContext,
    whoop: ResourceParam[WhoopDataSource],
    paths: PipelinePathsResource,
) -> RawWhoopPull:
    """Fetch the incremental window's records via the injected, swappable data source."""
    end = datetime.now(UTC)
    last_synced_date = read_sync_state(data_dir=paths.data_dir)
    start = determine_sync_start(today=end.date(), last_synced_date=last_synced_date)
    records = whoop.fetch_all(start, end)
    context.add_output_metadata(
        {name: MetadataValue.int(len(items)) for name, items in records.items()}
    )
    return RawWhoopPull(pull_date=end.date(), records=records)


@asset
def bronze_partitions(
    context: AssetExecutionContext,
    raw_whoop_data: RawWhoopPull,
    paths: PipelinePathsResource,
) -> RawWhoopPull:
    """Write atomic bronze JSONL partitions and advance sync state.

    Passes the records through unchanged so silver_frames doesn't need to re-read them from
    disk -- the bronze write is a durability side effect, not the only copy of the data.
    """
    output_paths = write_bronze_pull(
        raw_whoop_data.records, pull_date=raw_whoop_data.pull_date, data_dir=paths.data_dir
    )
    update_sync_state(raw_whoop_data.pull_date, data_dir=paths.data_dir)
    context.add_output_metadata(
        {name: MetadataValue.path(str(path)) for name, path in output_paths.items()}
    )
    return raw_whoop_data


@asset
def silver_frames(bronze_partitions: RawWhoopPull) -> SilverFrames:
    """Flatten raw records into typed DataFrames using Phase 1's tested transforms."""
    records = bronze_partitions.records
    return SilverFrames(
        cycles=flatten_cycles(records["cycles"]),
        recovery=flatten_recovery(records["recovery"]),
        sleep=flatten_sleep(records["sleep"]),
        workouts=flatten_workouts(records["workouts"]),
    )


@asset
def gold_tables(
    context: AssetExecutionContext,
    silver_frames: SilverFrames,
    paths: PipelinePathsResource,
) -> None:
    """Validate (Pandera) and idempotently upsert into DuckDB via Phase 1's loader."""
    output_path = load_silver_frames(
        silver_frames.cycles,
        silver_frames.recovery,
        silver_frames.sleep,
        silver_frames.workouts,
        database_path=paths.database_path,
    )
    context.add_output_metadata({"database_path": MetadataValue.path(str(output_path))})


__all__ = [
    "RawWhoopPull",
    "SilverFrames",
    "bronze_partitions",
    "gold_tables",
    "raw_whoop_data",
    "silver_frames",
]
