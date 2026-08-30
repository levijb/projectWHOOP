"""Incremental WHOOP collection orchestration with a date-partitioned bronze sink."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from whoop_pipeline.client import WhoopClient
from whoop_pipeline.storage.bronze import (
    DEFAULT_DATA_DIR,
    read_sync_state,
    update_sync_state,
    write_bronze_pull,
)


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Summary of a completed collection and its local partitions."""

    start: datetime
    end: datetime
    record_counts: dict[str, int]
    output_paths: dict[str, Path]


def determine_sync_start(
    *,
    today: date,
    last_synced_date: date | None,
    initial_days_back: int = 180,
) -> datetime:
    """Choose a UTC start, overlapping the last date so pending scores can mature."""
    if initial_days_back < 0:
        raise ValueError("initial_days_back must be non-negative")
    start_date = last_synced_date or (today - timedelta(days=initial_days_back))
    return datetime.combine(start_date, time.min, tzinfo=UTC)


def sync_to_bronze(
    client: WhoopClient,
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    now: datetime | None = None,
    initial_days_back: int = 180,
) -> SyncResult:
    """Fetch four collections, persist all partitions, then advance state.

    State changes only after every endpoint and file write succeeds, so a partial failure is
    retried on the next invocation instead of being silently skipped.
    """
    end = now or datetime.now(UTC)
    if end.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    end = end.astimezone(UTC)
    last_synced_date = read_sync_state(data_dir=data_dir)
    start = determine_sync_start(
        today=end.date(),
        last_synced_date=last_synced_date,
        initial_days_back=initial_days_back,
    )
    records = {
        "cycles": client.get_all_pages(client.get_cycle_collection, start, end),
        "recovery": client.get_all_pages(client.get_recovery_collection, start, end),
        "sleep": client.get_all_pages(client.get_sleep_collection, start, end),
        "workouts": client.get_all_pages(client.get_workout_collection, start, end),
    }
    output_paths = write_bronze_pull(records, pull_date=end.date(), data_dir=data_dir)
    update_sync_state(end.date(), data_dir=data_dir)
    return SyncResult(
        start=start,
        end=end,
        record_counts={name: len(items) for name, items in records.items()},
        output_paths=output_paths,
    )
