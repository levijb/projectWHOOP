"""Credential-safe Postgres URL parsing shared by storage, migrations, and dbt."""

from __future__ import annotations

import os
from collections.abc import Mapping

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

POSTGRES_SCHEMA = "whoop"


def is_enabled(flag_name: str) -> bool:
    return os.environ.get(flag_name, "").strip().lower() in ("1", "true", "yes")


def require_postgres_opt_in() -> str:
    """Return the URL only after explicit opt-in; never load a dotenv file."""
    if not is_enabled("WHOOP_PIPELINE_USE_POSTGRES"):
        raise RuntimeError("Set WHOOP_PIPELINE_USE_POSTGRES=true to explicitly enable Postgres")
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("WHOOP_PIPELINE_USE_POSTGRES is set but DATABASE_URL is not")
    return database_url


def postgres_url(database_url: str) -> URL:
    """Decode URL credentials once, require TLS, and reject unsupported connection options.

    SQLAlchemy and dbt must address the same database with the same TLS settings. Never echo
    an invalid URL in an error (SQLAlchemy's parser may otherwise include the input).
    """
    try:
        url = make_url(database_url)
    except (ValueError, TypeError, ArgumentError):
        # ArgumentError is not a ValueError. Keep third-party parser messages out of logs.
        raise ValueError("DATABASE_URL is not a valid Postgres connection URL") from None
    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg2"}:
        raise ValueError("DATABASE_URL must use postgres, postgresql, or postgresql+psycopg2")
    if not url.host or not url.username or not url.database:
        raise ValueError("DATABASE_URL is missing a host, user, or database name")
    allowed = {"sslmode", "sslrootcert", "connect_timeout"}
    if set(url.query) - allowed or any(not isinstance(v, str) for v in url.query.values()):
        raise ValueError(
            "DATABASE_URL supports only sslmode, sslrootcert, and connect_timeout options"
        )
    query = {"sslmode": "require", "connect_timeout": "10", **url.query}
    if query["sslmode"] not in {"require", "verify-ca", "verify-full"}:
        raise ValueError("DATABASE_URL must require TLS (require, verify-ca, or verify-full)")
    try:
        timeout = int(str(query["connect_timeout"]))
    except ValueError:
        raise ValueError("DATABASE_URL connect_timeout must be a positive integer") from None
    if timeout <= 0:
        raise ValueError("DATABASE_URL connect_timeout must be a positive integer")
    return url.set(drivername="postgresql+psycopg2", query=query)


def dbt_postgres_environment(database_url: str) -> dict[str, str]:
    """Build child-process environment values without shell exports or credential files."""
    url = postgres_url(database_url)
    return {
        "WHOOP_PGHOST": url.host or "",
        "WHOOP_PGPORT": str(url.port or 5432),
        "WHOOP_PGUSER": url.username or "",
        "DBT_ENV_SECRET_WHOOP_PASSWORD": url.password or "",
        "WHOOP_PGDATABASE": url.database or "",
        "WHOOP_PGSSLMODE": str(url.query["sslmode"]),
        "WHOOP_PGSSLROOTCERT": str(url.query.get("sslrootcert", "")),
        "WHOOP_PGCONNECT_TIMEOUT": str(url.query["connect_timeout"]),
    }


def dbt_environment(*, target: str, environ: Mapping[str, str]) -> dict[str, str]:
    env = dict(environ)
    env["DBT_SEND_ANONYMOUS_USAGE_STATS"] = "false"
    if target == "prod":
        env.update(dbt_postgres_environment(require_postgres_opt_in()))
    return env
