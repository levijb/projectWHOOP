from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from whoop_pipeline.dbt_cli import main
from whoop_pipeline.storage.database import dbt_postgres_environment, postgres_url


def test_url_decodes_credentials_once_and_preserves_tls_options() -> None:
    values = dbt_postgres_environment(
        "postgresql://alice%40tenant:p%40ss%25%3A%2F%23@db.invalid:6543/whoop_prod"
        "?sslmode=verify-full&sslrootcert=%2Fcerts%2Froot.crt&connect_timeout=20"
    )
    assert values["WHOOP_PGHOST"] == "db.invalid"
    assert values["WHOOP_PGPORT"] == "6543"
    assert values["WHOOP_PGUSER"] == "alice@tenant"
    assert values["DBT_ENV_SECRET_WHOOP_PASSWORD"] == "p@ss%:/#"
    assert values["WHOOP_PGDATABASE"] == "whoop_prod"
    assert values["WHOOP_PGSSLMODE"] == "verify-full"
    assert values["WHOOP_PGSSLROOTCERT"] == "/certs/root.crt"
    assert values["WHOOP_PGCONNECT_TIMEOUT"] == "20"


def test_url_defaults_port_and_requires_tls() -> None:
    exports = dbt_postgres_environment("postgres://alice:fake@db.invalid/whoop")
    assert exports["WHOOP_PGPORT"] == "5432"
    assert exports["WHOOP_PGSSLMODE"] == "require"
    assert postgres_url("postgres://a:b@db.invalid/d").drivername == "postgresql+psycopg2"


@pytest.mark.parametrize(
    "url",
    [
        "private-invalid-secret",
        "sqlite:///test.db",
        "postgresql://alice:fake@db.invalid/",
        "postgresql://alice:fake@db.invalid:bad/db",
        "postgresql://alice:fake@db.invalid/db?sslmode=disable",
        "postgresql://alice:fake@db.invalid/db?options=-csearch_path%3Dother",
        "postgresql://alice:fake@db.invalid/db?connect_timeout=0",
        "postgresql://alice:fake@db.invalid/db?sslmode=require&sslmode=disable",
    ],
)
def test_rejects_unsupported_urls_without_disclosing_them(url: str) -> None:
    with pytest.raises(ValueError) as error:
        postgres_url(url)
    assert url not in str(error.value)
    assert "private-invalid-secret" not in str(error.value)


def test_dbt_cli_requires_explicit_opt_in_and_never_prints_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:secret-marker@db.invalid/db")
    monkeypatch.delenv("WHOOP_PIPELINE_USE_POSTGRES", raising=False)
    with patch("whoop_pipeline.dbt_cli.subprocess.run") as run:
        assert main(["build", "--target", "prod"]) == 2
    run.assert_not_called()
    assert "secret-marker" not in capsys.readouterr().err


def test_dbt_cli_passes_credentials_in_child_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:secret-marker@db.invalid/db")
    monkeypatch.setenv("WHOOP_PIPELINE_USE_POSTGRES", "true")
    with patch("whoop_pipeline.dbt_cli.subprocess.run") as run:
        run.return_value.returncode = 0
        assert main(["build", "--target", "prod"]) == 0
    assert "secret-marker" not in str(run.call_args.args)
    assert run.call_args.kwargs["env"]["DBT_ENV_SECRET_WHOOP_PASSWORD"] == "secret-marker"
    assert "DBT_ENV_SECRET_WHOOP_PASSWORD" not in os.environ
