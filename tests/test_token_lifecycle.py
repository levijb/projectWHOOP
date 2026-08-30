"""Unit tests for ensure_fresh_token: the expiry-check + refresh + persistence logic that
LiveWhoopResource uses before every real pull. The WHOOP token endpoint is mocked via
`responses`; nothing here makes a real network call or uses a real token store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import responses

from whoop_pipeline.config import WhoopConfig
from whoop_pipeline.oauth import TOKEN_URL, OAuthError, WhoopTokenPair, ensure_fresh_token


class FakeTokenStore:
    """Minimal in-memory WhoopTokenStore, for asserting what gets persisted."""

    def __init__(self, initial: WhoopTokenPair | None = None) -> None:
        self.saved: WhoopTokenPair | None = initial
        self.save_calls = 0

    def read_tokens(self) -> WhoopTokenPair | None:
        return self.saved

    def save_tokens(self, tokens: WhoopTokenPair) -> None:
        self.saved = tokens
        self.save_calls += 1


def _config() -> WhoopConfig:
    return WhoopConfig(
        client_id="offline-client-id",
        client_secret="offline-client-secret",
        access_token="bootstrap-access-token",
        refresh_token="bootstrap-refresh-token",
    )


@responses.activate
def test_unexpired_stored_token_is_reused_without_refreshing() -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    store = FakeTokenStore(
        WhoopTokenPair(
            access_token="still-good",
            refresh_token="still-good-refresh",
            expires_at=now + timedelta(hours=1),
        )
    )

    result = ensure_fresh_token(_config(), token_store=store, now=now)

    assert result.access_token == "still-good"
    assert len(responses.calls) == 0
    assert store.save_calls == 0


@responses.activate
def test_expired_stored_token_triggers_refresh_and_persists_result() -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    store = FakeTokenStore(
        WhoopTokenPair(
            access_token="stale",
            refresh_token="stale-refresh",
            expires_at=now - timedelta(minutes=1),
        )
    )
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "refreshed-access",
            "refresh_token": "refreshed-refresh",
            "expires_in": 3600,
        },
    )

    result = ensure_fresh_token(_config(), token_store=store, now=now)

    assert result.access_token == "refreshed-access"
    assert result.refresh_token == "refreshed-refresh"
    assert result.expires_at == now + timedelta(seconds=3600)
    assert len(responses.calls) == 1
    assert responses.calls[0].request.body is not None
    assert "stale-refresh" in str(responses.calls[0].request.body)
    assert store.save_calls == 1
    assert store.saved == result


@responses.activate
def test_token_within_expiry_buffer_is_treated_as_expired() -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    store = FakeTokenStore(
        WhoopTokenPair(
            access_token="about-to-expire",
            refresh_token="about-to-expire-refresh",
            # 1 minute left, inside the 5-minute EXPIRY_BUFFER.
            expires_at=now + timedelta(minutes=1),
        )
    )
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "refreshed-access",
            "refresh_token": "refreshed-refresh",
            "expires_in": 3600,
        },
    )

    result = ensure_fresh_token(_config(), token_store=store, now=now)

    assert result.access_token == "refreshed-access"
    assert len(responses.calls) == 1


@responses.activate
def test_no_stored_token_refreshes_from_bootstrap_env_vars() -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    store = FakeTokenStore(initial=None)
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "refreshed-access",
            "refresh_token": "refreshed-refresh",
            "expires_in": 3600,
        },
    )

    result = ensure_fresh_token(_config(), token_store=store, now=now)

    assert result.access_token == "refreshed-access"
    assert responses.calls[0].request.body is not None
    assert "bootstrap-refresh-token" in str(responses.calls[0].request.body)
    assert store.saved == result


@responses.activate
def test_no_token_store_never_persists_but_still_refreshes() -> None:
    """Local dev / no Postgres configured: refresh still happens, nothing is ever saved."""
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={
            "access_token": "refreshed-access",
            "refresh_token": "refreshed-refresh",
            "expires_in": 3600,
        },
    )

    result = ensure_fresh_token(_config(), token_store=None, now=now)

    assert result.access_token == "refreshed-access"
    assert len(responses.calls) == 1


@responses.activate
def test_refresh_response_without_rotated_token_does_not_corrupt_store() -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    store = FakeTokenStore(
        WhoopTokenPair(
            access_token="stale", refresh_token="keep-me", expires_at=now - timedelta(minutes=1)
        )
    )
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"access_token": "refreshed-access", "expires_in": 3600},
    )

    with pytest.raises(OAuthError, match="rotated refresh_token"):
        ensure_fresh_token(_config(), token_store=store, now=now)
    assert store.save_calls == 0
    assert store.saved.refresh_token == "keep-me"


@pytest.mark.parametrize(
    "expires_in", [None, 0, -1, True, "3600", float("nan"), float("inf"), 1e100]
)
@responses.activate
def test_invalid_refresh_expiry_leaves_previous_pair_intact(expires_in: object) -> None:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    previous = WhoopTokenPair("old", "old-refresh", now - timedelta(hours=1))
    store = FakeTokenStore(previous)
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"access_token": "new", "refresh_token": "new-refresh", "expires_in": expires_in},
    )
    with pytest.raises(OAuthError):
        ensure_fresh_token(_config(), token_store=store, now=now)
    assert store.saved == previous
    assert store.save_calls == 0


@responses.activate
def test_failed_refresh_keeps_stored_pair_and_redacts_server_payload() -> None:
    store = FakeTokenStore(
        WhoopTokenPair("private-access", "private-refresh", datetime(2000, 1, 1, tzinfo=UTC))
    )
    responses.add(responses.POST, TOKEN_URL, status=400, json={"error": "private-refresh"})
    with pytest.raises(OAuthError) as error:
        ensure_fresh_token(_config(), token_store=store)
    assert "private-refresh" not in str(error.value)
    assert store.save_calls == 0
    assert len(responses.calls) == 1
    assert "private-access" not in repr(store.saved)


@responses.activate
def test_bootstrap_needs_only_refresh_token_not_access_token() -> None:
    store = FakeTokenStore()
    config = WhoopConfig(
        client_id="fake-id", client_secret="fake-secret", refresh_token="bootstrap"
    )
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"access_token": "new", "refresh_token": "rotated", "expires_in": 3600},
    )
    ensure_fresh_token(config, token_store=store)
    assert store.saved.refresh_token == "rotated"
    assert "scope=offline" in str(responses.calls[0].request.body)


@responses.activate
def test_rotation_survives_new_database_backend_and_stale_bootstrap(backend: object) -> None:
    from whoop_pipeline.storage.postgres_backend import PostgresBackend

    assert isinstance(backend, PostgresBackend)
    now = datetime(2026, 8, 30, 12, tzinfo=UTC)
    url = str(backend.engine.url)
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"access_token": "first-access", "refresh_token": "first-refresh", "expires_in": 3600},
    )
    ensure_fresh_token(_config(), token_store=backend, now=now)
    backend.close()
    restarted = PostgresBackend(url)
    try:
        responses.add(
            responses.POST,
            TOKEN_URL,
            json={
                "access_token": "second-access",
                "refresh_token": "second-refresh",
                "expires_in": 3600,
            },
        )
        current = ensure_fresh_token(_config(), token_store=restarted, now=now + timedelta(hours=2))
        assert current.refresh_token == "second-refresh"
        assert "first-refresh" in str(responses.calls[-1].request.body)
        assert "bootstrap-refresh-token" not in str(responses.calls[-1].request.body)
    finally:
        restarted.close()


def test_live_resource_reuses_persisted_pair_without_bootstrap_tokens(
    backend: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from whoop_pipeline.orchestration.resources import LiveWhoopResource
    from whoop_pipeline.storage.postgres_backend import PostgresBackend

    assert isinstance(backend, PostgresBackend)
    backend.save_tokens(
        WhoopTokenPair("stored-access", "stored-refresh", datetime(2100, 1, 1, tzinfo=UTC))
    )
    client = MagicMock()
    client.get_all_pages.return_value = []
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr("whoop_pipeline.orchestration.resources.WhoopClient", constructor)
    resource = LiveWhoopResource(database_url=str(backend.engine.url))
    now = datetime.now(UTC)
    result = resource.fetch_all(now, now)
    constructor.assert_called_once_with("stored-access")
    assert set(result) == {"cycles", "recovery", "sleep", "workouts"}


def test_live_local_resource_does_not_refresh_or_open_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from whoop_pipeline.orchestration.resources import LiveWhoopResource

    monkeypatch.setenv("WHOOP_ACCESS_TOKEN", "local-access")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:secret@db.invalid/whoop")
    client = MagicMock()
    client.get_all_pages.return_value = []
    constructor = MagicMock(return_value=client)
    refresh = MagicMock(side_effect=AssertionError("Local pulls must not rotate tokens"))
    monkeypatch.setattr("whoop_pipeline.orchestration.resources.WhoopClient", constructor)
    monkeypatch.setattr("whoop_pipeline.orchestration.resources.ensure_fresh_token", refresh)
    now = datetime.now(UTC)
    LiveWhoopResource().fetch_all(now, now)
    constructor.assert_called_once_with("local-access")
    refresh.assert_not_called()
