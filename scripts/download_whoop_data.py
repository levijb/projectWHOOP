"""Local executable wrapper around :mod:`whoop_pipeline.cli`."""

from whoop_pipeline.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
