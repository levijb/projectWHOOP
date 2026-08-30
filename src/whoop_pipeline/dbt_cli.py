"""Run dbt with a gated production profile derived from DATABASE_URL, never shell exports."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from whoop_pipeline.storage.database import dbt_environment


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("dev", "prod"), default="dev")
    args, dbt_args = parser.parse_known_args(argv)
    project_dir = Path(os.environ.get("WHOOP_DBT_PROJECT_DIR", "dbt")).resolve()
    try:
        env = dbt_environment(target=args.target, environ=os.environ)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    executable = shutil.which("dbt")
    if executable is None:
        print("dbt executable not found; install the project dependencies", file=sys.stderr)
        return 2
    return subprocess.run(
        [
            executable,
            *(dbt_args or ["build"]),
            "--project-dir",
            str(project_dir),
            "--profiles-dir",
            str(project_dir),
            "--target",
            args.target,
        ],
        env=env,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
