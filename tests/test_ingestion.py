from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from whoop_pipeline.client import WhoopClient
from whoop_pipeline.ingestion import determine_sync_start, sync_to_bronze
from whoop_pipeline.storage.bronze import read_sync_state, update_sync_state


def test_determine_sync_start_initial_and_incremental_overlap() -> None:
    today = date(2026, 8, 29)
    assert determine_sync_start(
        today=today, last_synced_date=None, initial_days_back=10
    ).date() == date(2026, 8, 19)
    assert determine_sync_start(today=today, last_synced_date=date(2026, 8, 28)).date() == date(
        2026, 8, 28
    )


def test_sync_writes_all_types_then_advances_state(tmp_path: Path) -> None:
    update_sync_state(date(2026, 8, 28), data_dir=tmp_path)
    client = MagicMock(spec=WhoopClient)
    client.get_all_pages.side_effect = [
        [{"id": 1}],
        [{"cycle_id": 1}],
        [{"id": "sleep"}],
        [{"id": "workout"}],
    ]
    now = datetime(2026, 8, 29, 12, tzinfo=UTC)

    result = sync_to_bronze(client, data_dir=tmp_path, now=now)

    assert result.start.date() == date(2026, 8, 28)
    assert result.record_counts == {"cycles": 1, "recovery": 1, "sleep": 1, "workouts": 1}
    assert all(path.exists() for path in result.output_paths.values())
    assert read_sync_state(data_dir=tmp_path) == date(2026, 8, 29)


def test_failed_sync_does_not_advance_state(tmp_path: Path) -> None:
    previous = date(2026, 8, 28)
    update_sync_state(previous, data_dir=tmp_path)
    client = MagicMock(spec=WhoopClient)
    client.get_all_pages.side_effect = RuntimeError("offline simulated failure")

    with pytest.raises(RuntimeError, match="simulated failure"):
        sync_to_bronze(
            client,
            data_dir=tmp_path,
            now=datetime(2026, 8, 29, 12, tzinfo=UTC),
        )

    assert read_sync_state(data_dir=tmp_path) == previous
