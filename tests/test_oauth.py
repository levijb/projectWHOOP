from __future__ import annotations

import responses

from whoop_pipeline.oauth import TOKEN_URL, refresh_access_token


@responses.activate
def test_refresh_access_token_is_mocked_and_noninteractive() -> None:
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"access_token": "offline-new-token", "refresh_token": "offline-next-refresh"},
    )

    payload = refresh_access_token(
        "offline-client-id", "offline-client-secret", "offline-refresh-token"
    )

    assert payload["access_token"] == "offline-new-token"
    assert len(responses.calls) == 1
