"""Manual-only WHOOP OAuth bootstrap; never used by tests or CI."""

from whoop_pipeline.config import WhoopConfig
from whoop_pipeline.oauth import get_whoop_token_pair


def main() -> int:
    config = WhoopConfig.from_env(
        required=("WHOOP_CLIENT_ID", "WHOOP_CLIENT_SECRET"),
    )
    if config.client_id is None or config.client_secret is None:
        raise RuntimeError("Validated WHOOP OAuth configuration unexpectedly missing")
    tokens = get_whoop_token_pair(
        config.client_id,
        config.client_secret,
        "http://localhost:3000/callback",
    )
    print("Authorization succeeded. Store these bootstrap values securely; never commit them:")
    print(f"WHOOP_ACCESS_TOKEN={tokens.access_token}")
    print(f"WHOOP_REFRESH_TOKEN={tokens.refresh_token}")
    print(f"Access token expires at {tokens.expires_at.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
