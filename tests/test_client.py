from __future__ import annotations

from datetime import UTC, datetime

import pytest
import requests
import responses

from whoop_pipeline.client import WhoopAPIError, WhoopClient, WhoopRateLimitError

START = datetime(2022, 4, 1, tzinfo=UTC)
END = datetime(2022, 5, 1, tzinfo=UTC)


@responses.activate
def test_pagination_uses_next_token_and_collects_all_records() -> None:
    url = f"{WhoopClient.base_url}/cycle"
    responses.add(
        responses.GET,
        url,
        json={"records": [{"id": 1}], "next_token": "page-two"},
        headers={
            "X-RateLimit-Limit": "100",
            "X-RateLimit-Remaining": "99",
            "X-RateLimit-Reset": "1",
        },
    )
    responses.add(responses.GET, url, json={"records": [{"id": 2}]})
    client = WhoopClient("offline-test-token", session=requests.Session())

    records = client.get_all_pages(client.get_cycle_collection, START, END)

    assert records == [{"id": 1}, {"id": 2}]
    assert len(responses.calls) == 2
    assert "nextToken=page-two" in responses.calls[1].request.url
    assert responses.calls[0].request.headers["Authorization"] == "Bearer offline-test-token"


@responses.activate
def test_429_uses_reset_header_then_retries() -> None:
    url = f"{WhoopClient.base_url}/user/profile/basic"
    responses.add(responses.GET, url, status=429, headers={"X-RateLimit-Reset": "1.25"})
    responses.add(responses.GET, url, json={"user_id": 10129})
    slept: list[float] = []
    client = WhoopClient("offline-test-token", max_retries=1, sleep=slept.append)

    assert client.get_user_profile() == {"user_id": 10129}
    assert slept == [1.25]
    assert len(responses.calls) == 2


@responses.activate
def test_missing_reset_header_uses_injected_exponential_backoff() -> None:
    url = f"{WhoopClient.base_url}/user/profile/basic"
    responses.add(responses.GET, url, status=429)
    responses.add(responses.GET, url, json={"user_id": 10129})
    slept: list[float] = []
    client = WhoopClient(
        "offline-test-token",
        max_retries=1,
        sleep=slept.append,
        random_uniform=lambda _low, _high: 0.5,
    )

    client.get_user_profile()

    assert slept == [1.5]


@responses.activate
def test_permanent_http_failure_raises_without_hanging() -> None:
    url = f"{WhoopClient.base_url}/user/profile/basic"
    responses.add(responses.GET, url, status=500)
    client = WhoopClient("offline-test-token", max_retries=5)

    with pytest.raises(WhoopAPIError, match="HTTP 500"):
        client.get_user_profile()
    assert len(responses.calls) == 1


@responses.activate
def test_persistent_rate_limit_raises_after_bounded_retries() -> None:
    url = f"{WhoopClient.base_url}/user/profile/basic"
    responses.add(responses.GET, url, status=429, headers={"X-RateLimit-Reset": "0"})
    responses.add(responses.GET, url, status=429, headers={"X-RateLimit-Reset": "0"})
    client = WhoopClient("offline-test-token", max_retries=1, sleep=lambda _seconds: None)

    with pytest.raises(WhoopRateLimitError, match="after 2 attempts"):
        client.get_user_profile()


def test_pagination_rejects_malformed_records() -> None:
    client = WhoopClient("offline-test-token")

    def endpoint(
        _start: datetime, _end: datetime, _limit: int, _token: str | None
    ) -> dict[str, object]:
        return {"records": "not-a-list"}

    with pytest.raises(WhoopAPIError, match="invalid records"):
        client.get_all_pages(endpoint, START, END)
