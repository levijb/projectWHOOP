from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from whoop_pipeline.storage.bronze import (
    read_sync_state,
    update_sync_state,
    write_bronze_records,
)


def test_bronze_jsonl_round_trip_and_same_date_replacement(tmp_path: Path) -> None:
    destination = write_bronze_records(
        "cycles", [{"id": 1}, {"id": 2}], pull_date=date(2026, 8, 28), data_dir=tmp_path
    )
    rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"id": 1}, {"id": 2}]

    write_bronze_records("cycles", [{"id": 3}], pull_date=date(2026, 8, 28), data_dir=tmp_path)
    rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"id": 3}]


def test_sync_state_read_update_and_invalid_state(tmp_path: Path) -> None:
    assert read_sync_state(data_dir=tmp_path) is None
    state_path = update_sync_state(date(2026, 8, 28), data_dir=tmp_path)
    assert read_sync_state(data_dir=tmp_path) == date(2026, 8, 28)

    state_path.write_text('{"last_synced_date":"not-a-date"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid sync state"):
        read_sync_state(data_dir=tmp_path)


def test_bronze_rejects_unknown_record_type(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown bronze record type"):
        write_bronze_records("profile", [], data_dir=tmp_path)
