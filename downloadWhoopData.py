"""Backward-compatible CLI path; new code should use ``scripts/download_whoop_data.py``."""

from whoop_pipeline.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
