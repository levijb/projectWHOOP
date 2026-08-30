"""Manual-only WHOOP OAuth bootstrap; never used by tests or CI."""

from whoop_pipeline.config import WhoopConfig
from whoop_pipeline.oauth import get_whoop_access_token


def main() -> int:
    config = WhoopConfig.from_env(
        required=("WHOOP_CLIENT_ID", "WHOOP_CLIENT_SECRET"),
    )
    if config.client_id is None or config.client_secret is None:
        raise RuntimeError("Validated WHOOP OAuth configuration unexpectedly missing")
    token = get_whoop_access_token(
        config.client_id,
        config.client_secret,
        "http://localhost:3000/callback",
    )
    print("Authorization succeeded. Store this access token in your local .env only:")
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
