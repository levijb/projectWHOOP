"""HTTP client for WHOOP Developer API v2.

The client has no credential discovery side effects: callers provide a token explicitly. A
``requests.Session`` and sleeper can be injected so every transport and retry path is testable
without network access or real delays.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)
JsonObject = dict[str, Any]
CollectionMethod = Callable[[datetime, datetime, int, str | None], JsonObject | None]


class WhoopAPIError(RuntimeError):
    """Raised when WHOOP returns a non-retryable or permanently failing response."""


class WhoopRateLimitError(WhoopAPIError):
    """Raised after the client exhausts its bounded retries for HTTP 429 responses."""


class WhoopClient:
    """Client for WHOOP API v2 with bounded, header-aware rate-limit retries."""

    base_url = "https://api.prod.whoop.com/developer/v2"

    def __init__(
        self,
        access_token: str,
        session: requests.Session | None = None,
        *,
        max_retries: int = 3,
        timeout_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if not access_token:
            raise ValueError("access_token must not be empty")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.access_token = access_token
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self._sleep = sleep
        self._random_uniform = random_uniform

    def _get_headers(self) -> dict[str, str]:
        """Return authentication and content-negotiation headers."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    def _make_request(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> JsonObject | None:
        """Make one logical GET, retrying only rate limits and transport failures.

        A bounded retry loop avoids both silent partial pulls and infinite hangs. HTTP errors
        other than 429 fail immediately because retrying authentication or malformed requests
        would hide the actionable cause.
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        last_transport_error: requests.RequestException | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    headers=self._get_headers(),
                    params=params,
                    timeout=self.timeout_seconds,
                )
            except requests.RequestException as exc:
                last_transport_error = exc
                if attempt >= self.max_retries:
                    raise WhoopAPIError(
                        f"WHOOP request to {endpoint!r} failed after {attempt + 1} attempts"
                    ) from exc
                self._sleep(self._fallback_delay(attempt))
                continue

            self._log_rate_limit(response)
            if response.status_code == 429:
                if attempt >= self.max_retries:
                    raise WhoopRateLimitError(
                        f"WHOOP rate limit persisted after {attempt + 1} attempts for {endpoint!r}"
                    )
                self._sleep(self._retry_delay(response, attempt))
                continue

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                raise WhoopAPIError(
                    f"WHOOP request to {endpoint!r} failed with HTTP {response.status_code}"
                ) from exc

            try:
                payload = response.json()
            except requests.exceptions.JSONDecodeError as exc:
                raise WhoopAPIError(f"WHOOP returned invalid JSON for {endpoint!r}") from exc
            if payload is None:
                return None
            if not isinstance(payload, dict):
                raise WhoopAPIError(f"WHOOP returned a non-object JSON payload for {endpoint!r}")
            return payload

        raise WhoopAPIError(f"WHOOP request to {endpoint!r} failed") from last_transport_error

    def _log_rate_limit(self, response: requests.Response) -> None:
        limit = response.headers.get("X-RateLimit-Limit")
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if any(value is not None for value in (limit, remaining, reset)):
            LOGGER.debug(
                "WHOOP rate limit: limit=%s remaining=%s reset=%s", limit, remaining, reset
            )

    def _retry_delay(self, response: requests.Response, attempt: int) -> float:
        raw_reset = response.headers.get("X-RateLimit-Reset")
        if raw_reset:
            parsed_delay = self._parse_reset_delay(raw_reset)
            if parsed_delay is not None:
                return parsed_delay
        return self._fallback_delay(attempt)

    @staticmethod
    def _parse_reset_delay(raw_reset: str) -> float | None:
        """Interpret reset as seconds, Unix epoch seconds, or an HTTP date."""
        try:
            numeric = float(raw_reset)
        except ValueError:
            try:
                reset_at = parsedate_to_datetime(raw_reset)
            except (TypeError, ValueError, OverflowError):
                return None
            if reset_at.tzinfo is None:
                reset_at = reset_at.replace(tzinfo=UTC)
            return max(0.0, (reset_at - datetime.now(UTC)).total_seconds())

        now_epoch = time.time()
        if numeric > now_epoch - 60:
            return max(0.0, numeric - now_epoch)
        return max(0.0, numeric)

    def _fallback_delay(self, attempt: int) -> float:
        return min(float(2**attempt) + self._random_uniform(0.0, 1.0), 60.0)

    @staticmethod
    def _collection_params(
        start: datetime, end: datetime, limit: int, next_token: str | None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "limit": limit,
        }
        if next_token:
            params["nextToken"] = next_token
        return params

    def get_user_profile(self) -> JsonObject | None:
        return self._make_request("user/profile/basic")

    def get_body_measurement(self) -> JsonObject | None:
        return self._make_request("user/measurement/body")

    def get_cycle_collection(
        self,
        start: datetime,
        end: datetime,
        limit: int = 25,
        next_token: str | None = None,
    ) -> JsonObject | None:
        return self._make_request(
            "cycle", params=self._collection_params(start, end, limit, next_token)
        )

    def get_cycle_by_id(self, cycle_id: int) -> JsonObject | None:
        return self._make_request(f"cycle/{cycle_id}")

    def get_recovery_collection(
        self,
        start: datetime,
        end: datetime,
        limit: int = 25,
        next_token: str | None = None,
    ) -> JsonObject | None:
        # Recovery is also nested in getCycleById, but the empirically reliable collection
        # endpoint remains the primary ingestion path.
        return self._make_request(
            "recovery", params=self._collection_params(start, end, limit, next_token)
        )

    def get_sleep_collection(
        self,
        start: datetime,
        end: datetime,
        limit: int = 25,
        next_token: str | None = None,
    ) -> JsonObject | None:
        return self._make_request(
            "activity/sleep", params=self._collection_params(start, end, limit, next_token)
        )

    def get_sleep_by_id(self, sleep_id: str) -> JsonObject | None:
        return self._make_request(f"activity/sleep/{sleep_id}")

    def get_workout_collection(
        self,
        start: datetime,
        end: datetime,
        limit: int = 25,
        next_token: str | None = None,
    ) -> JsonObject | None:
        return self._make_request(
            "activity/workout", params=self._collection_params(start, end, limit, next_token)
        )

    def get_workout_by_id(self, workout_id: str) -> JsonObject | None:
        return self._make_request(f"activity/workout/{workout_id}")

    def get_all_pages(
        self,
        endpoint_method: CollectionMethod,
        start: datetime,
        end: datetime,
        limit: int = 25,
    ) -> list[JsonObject]:
        """Collect a paginated endpoint without arbitrary sleeps between successful pages."""
        all_records: list[JsonObject] = []
        next_token: str | None = None
        while True:
            response = endpoint_method(start, end, limit, next_token)
            if not response:
                break
            records = response.get("records", [])
            if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
                raise WhoopAPIError("WHOOP collection payload contains invalid records")
            all_records.extend(records)
            raw_token = response.get("next_token")
            next_token = str(raw_token) if raw_token else None
            if next_token is None:
                break
        return all_records

    def get_all_historical_data(self, days_back: int = 180) -> dict[str, Any]:
        if days_back < 0:
            raise ValueError("days_back must be non-negative")
        end = datetime.now(UTC)
        start = end - timedelta(days=days_back)
        return {
            "user": self.get_user_profile(),
            "body": self.get_body_measurement(),
            "cycles": self.get_all_pages(self.get_cycle_collection, start, end),
            "recovery": self.get_all_pages(self.get_recovery_collection, start, end),
            "sleep": self.get_all_pages(self.get_sleep_collection, start, end),
            "workouts": self.get_all_pages(self.get_workout_collection, start, end),
        }
