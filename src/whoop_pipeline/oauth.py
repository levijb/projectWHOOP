"""Interactive WHOOP OAuth helpers retained for manual, local use only.

No pipeline import invokes this module. The authorization-code flow must only be run by a
human after creating a WHOOP developer application; automated tests intentionally do not
exercise the browser/server path.
"""

from __future__ import annotations

import base64
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

AUTHORIZE_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = (
    "offline read:profile read:body_measurement read:cycles read:recovery read:sleep read:workout"
)


class OAuthError(RuntimeError):
    """Raised when the explicit manual OAuth flow cannot complete safely."""


def get_whoop_access_token(client_id: str, client_secret: str, redirect_uri: str) -> str:
    """Run the interactive authorization-code flow and return an access token.

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
    token_payload = _exchange_authorization_code(client_id, client_secret, redirect_uri, code)
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OAuthError("WHOOP token response did not contain access_token")
    return access_token


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
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise OAuthError("WHOOP token endpoint returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("access_token"), str):
        raise OAuthError("WHOOP refresh response did not contain access_token")
    return payload
