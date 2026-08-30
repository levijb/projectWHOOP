"""Date-partitioned JSONL bronze storage and atomic incremental sync state."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

BRONZE_RECORD_TYPES = ("cycles", "recovery", "sleep", "workouts")
DEFAULT_DATA_DIR = Path("data")


def write_bronze_records(
    record_type: str,
    records: Iterable[Mapping[str, Any]],
    *,
    pull_date: date | None = None,
    data_dir: str | Path = DEFAULT_DATA_DIR,
) -> Path:
    """Atomically replace one record type's JSONL partition for a pull date.

    Replacement makes retrying a same-day pull deterministic. Historical partitions remain
    untouched, retaining the API-shaped bronze boundary for future reprocessing.
    """
    if record_type not in BRONZE_RECORD_TYPES:
        allowed = ", ".join(BRONZE_RECORD_TYPES)
        raise ValueError(f"Unknown bronze record type {record_type!r}; expected one of: {allowed}")
    partition_date = pull_date or datetime.now(UTC).date()
    destination = Path(data_dir) / "bronze" / record_type / f"{partition_date.isoformat()}.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)

    lines = [json.dumps(dict(record), separators=(",", ":"), sort_keys=True) for record in records]
    _atomic_write_text(destination, "".join(f"{line}\n" for line in lines))
    return destination


def write_bronze_pull(
    records_by_type: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    pull_date: date | None = None,
    data_dir: str | Path = DEFAULT_DATA_DIR,
) -> dict[str, Path]:
    """Write the four expected collections for one pull."""
    missing = [name for name in BRONZE_RECORD_TYPES if name not in records_by_type]
    if missing:
        raise ValueError(f"Bronze pull is missing record type(s): {', '.join(missing)}")
    return {
        name: write_bronze_records(
            name, records_by_type[name], pull_date=pull_date, data_dir=data_dir
        )
        for name in BRONZE_RECORD_TYPES
    }


def read_sync_state(*, data_dir: str | Path = DEFAULT_DATA_DIR) -> date | None:
    """Return the last successful sync date, or ``None`` before the first sync."""
    state_path = Path(data_dir) / "_state" / "sync_state.json"
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        raw_date = state["last_synced_date"]
        return date.fromisoformat(raw_date)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid sync state file: {state_path}") from exc


def update_sync_state(last_synced_date: date, *, data_dir: str | Path = DEFAULT_DATA_DIR) -> Path:
    """Atomically record the date of a fully successful bronze pull."""
    state_path = Path(data_dir) / "_state" / "sync_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"last_synced_date": last_synced_date.isoformat()}, indent=2, sort_keys=True
    )
    _atomic_write_text(state_path, f"{payload}\n")
    return state_path


def _atomic_write_text(destination: Path, content: str) -> None:
    """Write beside the target then replace it, preventing torn JSON/JSONL files."""
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", text=True
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
