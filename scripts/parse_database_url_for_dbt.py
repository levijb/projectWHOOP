"""Compatibility entry point: run dbt with DATABASE_URL, without printing credentials."""

from whoop_pipeline.dbt_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
