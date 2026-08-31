"""Backend tests on a disposable SQLite file provisioned by the real Alembic revision.

Transactional operations are shared with production. Postgres dialect/view/TLS/permissions
still require the manual smoke test documented in SETUP.md.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandera.pandas as pa
import pytest

from whoop_pipeline.oauth import WhoopTokenPair
from whoop_pipeline.storage.postgres_backend import PostgresBackend
from whoop_pipeline.transform.flatten import (
    flatten_cycles,
    flatten_recovery,
    flatten_sleep,
    flatten_workouts,
)


def _frames(records: dict[str, list[dict[str, Any]]]) -> tuple[Any, Any, Any, Any]:
    return (
        flatten_cycles(records["cycles"]),
        flatten_recovery(records["recovery"]),
        flatten_sleep(records["sleep"]),
        flatten_workouts(records["workouts"]),
    )


def test_load_gold_is_idempotent(
    backend: PostgresBackend, fixture_records: dict[str, list[dict[str, Any]]]
) -> None:
    frames = _frames(fixture_records)

    backend.load_gold(*frames)
    backend.load_gold(*frames)

    with backend.engine.connect() as connection:
        counts = {
            table: connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar()
            for table in ("cycles", "recovery", "sleep", "workouts")
        }
    assert counts == {"cycles": 2, "recovery": 2, "sleep": 2, "workouts": 2}


def test_load_gold_updates_existing_id_instead_of_duplicating(
    backend: PostgresBackend, fixture_records: dict[str, list[dict[str, Any]]]
) -> None:
    frames = list(_frames(fixture_records))
    backend.load_gold(*frames)
    frames[0].loc[0, "strain"] = 6.5
    backend.load_gold(*frames)

    with backend.engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT COUNT(*), MAX(strain) FROM cycles WHERE cycle_id = 93845"
        ).fetchone()
    assert row is not None
    assert tuple(row) == (1, 6.5)


def test_load_gold_rejects_invalid_data_before_writing(
    backend: PostgresBackend, fixture_records: dict[str, list[dict[str, Any]]]
) -> None:
    frames = list(_frames(fixture_records))
    frames[1].loc[0, "recovery_score"] = 999

    with pytest.raises(pa.errors.SchemaErrors):
        backend.load_gold(*frames)

    with backend.engine.connect() as connection:
        count = connection.exec_driver_sql("SELECT COUNT(*) FROM recovery").scalar()
    assert count == 0


def test_sync_state_round_trips_and_defaults_to_none(backend: PostgresBackend) -> None:
    assert backend.read_sync_state() is None

    backend.update_sync_state(date(2026, 8, 28))
    assert backend.read_sync_state() == date(2026, 8, 28)

    backend.update_sync_state(date(2026, 8, 29))
    assert backend.read_sync_state() == date(2026, 8, 29)


def test_tokens_round_trip_and_default_to_none(backend: PostgresBackend) -> None:
    assert backend.read_tokens() is None

    pair = WhoopTokenPair(
        access_token="token-a",
        refresh_token="refresh-a",
        expires_at=datetime(2026, 8, 30, 12, tzinfo=UTC),
    )
    backend.save_tokens(pair)
    assert backend.read_tokens() == pair

    refreshed = WhoopTokenPair(
        access_token="token-b",
        refresh_token="refresh-b",
        expires_at=datetime(2026, 8, 30, 12, tzinfo=UTC) + timedelta(hours=1),
    )
    backend.save_tokens(refreshed)
    assert backend.read_tokens() == refreshed


@pytest.mark.parametrize("failed_table", ["workouts", "sync_state"])
def test_failed_transaction_preserves_all_gold_and_checkpoint(
    backend: PostgresBackend,
    fixture_records: dict[str, list[dict[str, Any]]],
    failed_table: str,
) -> None:
    from sqlalchemy import event

    frames = list(_frames(fixture_records))
    checkpoint = date(2026, 8, 28)
    backend.load_gold(*frames, last_synced_date=checkpoint)
    frames[0].loc[0, "strain"] = 6.5

    def fail_insert(
        connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if statement.startswith(f"INSERT INTO main.{failed_table}"):
            raise RuntimeError("simulated write failure")

    event.listen(backend.engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="simulated write failure"):
            backend.load_gold(*frames, last_synced_date=date(2026, 8, 30))
    finally:
        event.remove(backend.engine, "before_cursor_execute", fail_insert)
    assert backend.read_sync_state() == checkpoint
    with backend.engine.connect() as connection:
        strain = connection.exec_driver_sql(
            "SELECT strain FROM cycles WHERE cycle_id = 93845"
        ).scalar()
        assert strain == fixture_records["cycles"][0]["score"]["strain"]
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM workouts").scalar() == 2


def test_partial_retry_preserves_history_and_survives_a_new_backend(
    backend: PostgresBackend, fixture_records: dict[str, list[dict[str, Any]]]
) -> None:
    frames = _frames(fixture_records)
    backend.load_gold(*frames, last_synced_date=date(2026, 8, 28))
    partial = [frame.iloc[:1].copy() for frame in frames]
    partial[0].loc[0, "strain"] = 6.5
    backend.load_gold(*partial, last_synced_date=date(2026, 8, 30))
    url = str(backend.engine.url)
    backend.close()
    restarted = PostgresBackend(url)
    try:
        assert restarted.read_sync_state() == date(2026, 8, 30)
        with restarted.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM cycles").scalar() == 2
            assert (
                connection.exec_driver_sql(
                    "SELECT strain FROM cycles WHERE cycle_id = 93845"
                ).scalar()
                == 6.5
            )
    finally:
        restarted.close()


def test_empty_pull_and_nullable_values(
    backend: PostgresBackend, fixture_records: dict[str, list[dict[str, Any]]]
) -> None:
    frames = _frames(fixture_records)
    backend.load_gold(*frames)
    backend.load_gold(*(frame.iloc[:0] for frame in frames), last_synced_date=date(2026, 8, 30))
    assert backend.read_sync_state() == date(2026, 8, 30)
    with backend.engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM cycles").scalar() == 2
        assert (
            connection.exec_driver_sql(
                "SELECT recovery_score FROM recovery WHERE cycle_id = 93846"
            ).scalar()
            is None
        )
