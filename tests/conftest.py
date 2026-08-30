from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg2
import pytest
from alembic import command
from alembic.config import Config

from whoop_pipeline.storage.postgres_backend import PostgresBackend

FIXTURE_DIRECTORY = Path(__file__).parent / "fixtures"


def pytest_configure() -> None:
    """Sanitize before test collection/imports, including credentials inherited by dbt."""
    for key in list(os.environ):
        if key.startswith(("WHOOP_", "DBT_ENV_SECRET_", "PG")) or key == "DATABASE_URL":
            os.environ.pop(key, None)
    os.environ["DBT_SEND_ANONYMOUS_USAGE_STATS"] = "false"
    os.environ["DAGSTER_DISABLE_TELEMETRY"] = "true"


@pytest.fixture(autouse=True)
def no_live_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requests must be mocked, and psycopg2's native sockets must never reach Postgres."""

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Live network connections are forbidden in the offline test suite")

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(sock: socket.socket, address: Any) -> Any:
        # Windows asyncio implements socketpair with a TCP loopback connection.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        return blocked()

    def connect_ex(sock: socket.socket, address: Any) -> Any:
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect_ex(sock, address)
        return blocked()

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(psycopg2, "connect", blocked)
    # Guard both the library and the already-imported config alias against the real .env.
    monkeypatch.setattr("dotenv.load_dotenv", blocked)
    monkeypatch.setattr("whoop_pipeline.config.load_dotenv", blocked)


@pytest.fixture
def backend(tmp_path: Path) -> Iterator[PostgresBackend]:
    """Provision the real Alembic revision on SQLite; never use metadata.create_all."""
    instance = PostgresBackend(f"sqlite:///{tmp_path / 'postgres_substitute.db'}")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with instance.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def fixture_records() -> dict[str, list[dict[str, Any]]]:
    return {
        name: json.loads((FIXTURE_DIRECTORY / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("cycles", "recovery", "sleep", "workouts")
    }
