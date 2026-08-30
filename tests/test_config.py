from __future__ import annotations

import pytest

from whoop_pipeline.config import ENVIRONMENT_VARIABLES, WhoopConfig


def test_config_loads_only_when_explicitly_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WHOOP_ACCESS_TOKEN", "offline-test-token")

    config = WhoopConfig.from_env(env_file=None)

    assert config.require_access_token() == "offline-test-token"
    assert config.client_secret is None


def test_config_error_names_every_missing_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError) as error:
        WhoopConfig.from_env(required=("WHOOP_CLIENT_ID", "WHOOP_CLIENT_SECRET"), env_file=None)

    assert "WHOOP_CLIENT_ID" in str(error.value)
    assert "WHOOP_CLIENT_SECRET" in str(error.value)
