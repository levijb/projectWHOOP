"""Manual OAuth bootstrap and noninteractive token refresh; no import-time side effects."""

from __future__ import annotations

import base64
import json
import math
import secrets
import threading
import webbrowser
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from whoop_pipeline.config import WhoopConfig

AUTHORIZE_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = (
    "offline read:profile read:body_measurement read:cycles read:recovery read:sleep read:workout"
)

# Treat a token as expired this far ahead of its real expiry, so a request in flight doesn't
# race the token dying mid-call.
EXPIRY_BUFFER = timedelta(minutes=5)


class OAuthError(RuntimeError):
    """Raised when WHOOP authorization or token refresh cannot complete safely."""


def get_whoop_access_token(client_id: str, client_secret: str, redirect_uri: str) -> str:
    """Compatibility helper returning only the access token from the manual flow."""
    return get_whoop_token_pair(client_id, client_secret, redirect_uri).access_token


def get_whoop_token_pair(client_id: str, client_secret: str, redirect_uri: str) -> WhoopTokenPair:
    """Run the interactive authorization-code flow and retain both tokens and their expiry.

    This intentionally opens a real browser and is therefore never called by ingestion, CI,
    or tests. It remains a manual bootstrap helper only.
    """
    state = secrets.token_urlsafe(32)
    callback: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            callback["code"] = query.get("code", [""])[0]
            callback["state"] = query.get("state", [""])[0]
            callback["error"] = query.get("error", [""])[0]
            body = b"WHOOP authorization received. You can close this browser tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    parsed_redirect = urlparse(redirect_uri)
    if parsed_redirect.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("redirect_uri must use localhost for the manual OAuth helper")
    port = parsed_redirect.port or 3000
    server = HTTPServer((parsed_redirect.hostname or "localhost", port), CallbackHandler)
    server.timeout = 300
    authorization_url = f"{AUTHORIZE_URL}?{urlencode({'client_id': client_id, 'redirect_uri': redirect_uri, 'response_type': 'code', 'scope': SCOPES, 'state': state})}"
    webbrowser.open(authorization_url)
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()
    worker.join(timeout=305)
    server.server_close()

    if callback.get("error"):
        raise OAuthError(f"WHOOP authorization failed: {callback['error']}")
    if callback.get("state") != state:
        raise OAuthError("WHOOP OAuth state verification failed")
    code = callback.get("code")
    if not code:
        raise OAuthError("WHOOP OAuth callback did not contain an authorization code")
    issued_at = datetime.now(UTC)
    token_payload = _exchange_authorization_code(client_id, client_secret, redirect_uri, code)
    return WhoopTokenPair.from_token_response(token_payload, now=issued_at)


def _exchange_authorization_code(
    client_id: str, client_secret: str, redirect_uri: str, code: str
) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    response = requests.post(TOKEN_URL, data=data, timeout=30)
    if response.status_code >= 400:
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Authorization": f"Basic {basic}"},
            timeout=30,
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise OAuthError("WHOOP token endpoint returned an invalid payload")
    return payload


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict[str, Any]:
    """Exchange an existing refresh token without launching a browser."""
    try:
        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "offline",
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException:
        # Do not log request/response payloads or retry an ambiguous rotating-token exchange.
        raise OAuthError(
            "WHOOP token refresh failed; check authorization before retrying"
        ) from None
    try:
        payload = response.json()
    except json.JSONDecodeError:
        raise OAuthError("WHOOP token endpoint returned invalid JSON") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
        raise OAuthError("WHOOP refresh response did not contain access_token")
    return payload


@dataclass(frozen=True, slots=True)
class WhoopTokenPair:
    """An access/refresh token pair with a known (or assumed-expired) expiry."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.expires_at.tzinfo is None:
            raise ValueError("Token expires_at must be timezone-aware")

    @classmethod
    def from_token_response(cls, payload: dict[str, Any], *, now: datetime) -> WhoopTokenPair:
        """Build a pair from a WHOOP token endpoint response.

        WHOOP rotates both tokens. Reject an incomplete pair rather than persisting a known
        new access token with a potentially invalidated old refresh token.
        """
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthError("WHOOP token response did not contain access_token")
        expires_in = payload.get("expires_in")
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, int | float)
            or not math.isfinite(expires_in)
            or expires_in <= 0
        ):
            raise OAuthError("WHOOP token response did not contain a positive finite expires_in")
        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise OAuthError("WHOOP token response did not contain a rotated refresh_token")
        try:
            expires_at = now + timedelta(seconds=float(expires_in))
        except OverflowError:
            raise OAuthError("WHOOP token expiry is out of range") from None
        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )

    def is_expired(self, *, now: datetime, buffer: timedelta = EXPIRY_BUFFER) -> bool:
        return self.expires_at - buffer <= now


class WhoopTokenStore(Protocol):
    """Where the current token pair is persisted between runs. Postgres-backed in production;
    absent entirely for local dev/tests, in which case tokens are never persisted and every
    run re-establishes freshness from the bootstrap env vars."""

    def read_tokens(self) -> WhoopTokenPair | None: ...
    def save_tokens(self, tokens: WhoopTokenPair) -> None: ...


def ensure_fresh_token(
    config: WhoopConfig,
    *,
    token_store: WhoopTokenStore | None,
    now: datetime | None = None,
) -> WhoopTokenPair:
    """Return a token pair that is not within ``EXPIRY_BUFFER`` of expiring, refreshing first
    if necessary.

    Prefers whatever's currently stored (e.g. in Postgres) over the bootstrap env-var token,
    since a stored token may already be a later refresh than the original bootstrap value. A
    bootstrap token with no stored record has an unknown real expiry and is deliberately
    treated as already expired, so the very first run always establishes a real, tracked
    expiry via one refresh rather than guessing how long the bootstrap token has left.
    """
    now = now or datetime.now(UTC)
    stored = token_store.read_tokens() if token_store is not None else None
    current = stored or WhoopTokenPair(
        access_token=config.access_token or "",
        refresh_token=config.refresh_token or "",
        expires_at=datetime.fromtimestamp(0, tz=UTC),
    )
    if not current.is_expired(now=now):
        return current

    if not current.refresh_token:
        raise OAuthError("Access token is expired or unverified and no refresh token is available")
    if not config.client_id or not config.client_secret:
        raise OAuthError("WHOOP_CLIENT_ID/WHOOP_CLIENT_SECRET are required to refresh a token")

    payload = refresh_access_token(config.client_id, config.client_secret, current.refresh_token)
    refreshed = WhoopTokenPair.from_token_response(payload, now=now)
    if token_store is not None:
        token_store.save_tokens(refreshed)
    return refreshed
