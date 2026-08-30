"""Explicit environment configuration with no import-time validation or file access."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENVIRONMENT_VARIABLES = (
    "WHOOP_CLIENT_ID",
    "WHOOP_CLIENT_SECRET",
    "WHOOP_ACCESS_TOKEN",
    "WHOOP_REFRESH_TOKEN",
)


@dataclass(frozen=True, slots=True)
class WhoopConfig:
    """WHOOP credentials loaded only when ``from_env`` is called explicitly."""

    client_id: str | None = None
    client_secret: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None

    @classmethod
    def from_env(
        cls,
        *,
        required: tuple[str, ...] = ("WHOOP_ACCESS_TOKEN",),
        env_file: str | Path | None = ".env",
    ) -> WhoopConfig:
        """Load configuration and name every required variable that is absent.

        ``env_file=None`` is useful in hermetic processes that permit environment variables
        but intentionally prohibit reading a dotenv file.
        """
        unknown = sorted(set(required) - set(ENVIRONMENT_VARIABLES))
        if unknown:
            raise ValueError(f"Unknown WHOOP configuration variable(s): {', '.join(unknown)}")
        if env_file is not None:
            load_dotenv(dotenv_path=env_file, override=False)

        values = {name: os.environ.get(name) or None for name in ENVIRONMENT_VARIABLES}
        missing = [name for name in required if values[name] is None]
        if missing:
            raise ValueError(
                "Missing required environment variable(s): "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill it locally."
            )
        return cls(
            client_id=values["WHOOP_CLIENT_ID"],
            client_secret=values["WHOOP_CLIENT_SECRET"],
            access_token=values["WHOOP_ACCESS_TOKEN"],
            refresh_token=values["WHOOP_REFRESH_TOKEN"],
        )

    def require_access_token(self) -> str:
        """Return the access token or fail close to the call site with a clear message."""
        if self.access_token is None:
            raise ValueError("Missing required environment variable: WHOOP_ACCESS_TOKEN")
        return self.access_token
