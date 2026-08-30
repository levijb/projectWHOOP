"""Thin command-line entry point for a human-authorized local bronze sync."""

from __future__ import annotations

import argparse
from pathlib import Path

from whoop_pipeline.client import WhoopClient
from whoop_pipeline.config import WhoopConfig
from whoop_pipeline.ingestion import sync_to_bronze


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download WHOOP v2 records into local bronze JSONL"
    )
    parser.add_argument("--days-back", type=int, default=180, help="Initial history window")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Local data root")
    return parser


def main() -> int:
    """Load a pre-provisioned token and perform one explicit local sync."""
    args = build_parser().parse_args()
    config = WhoopConfig.from_env(required=("WHOOP_ACCESS_TOKEN",))
    client = WhoopClient(config.require_access_token())
    result = sync_to_bronze(client, data_dir=args.data_dir, initial_days_back=args.days_back)
    counts = ", ".join(f"{name}={count}" for name, count in result.record_counts.items())
    print(
        f"WHOOP sync completed for {result.start.isoformat()} to {result.end.isoformat()}: {counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
