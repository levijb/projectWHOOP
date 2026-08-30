# Orchestration: dbt marts + Dagster + Docker + scheduled CI

This describes the Phase 2 layer built on top of Phase 1's ingestion/storage/validation code
(see [docs/data_model.md](data_model.md) for that). Nothing here reimplements Phase 1 logic --
it wires already-tested functions into a graph and adds a feature-engineering layer on top.

## The asset graph

```text
raw_whoop_data -> bronze_partitions -> silver_frames -> gold_tables -> mart_daily_features
```

| Asset | What it does | Defined in |
|---|---|---|
| `raw_whoop_data` | Fetches the four WHOOP v2 collections for the incremental sync window, via the injected `whoop` resource. | `src/whoop_pipeline/orchestration/assets.py` |
| `bronze_partitions` | Writes atomic JSONL partitions and advances sync state (Phase 1's `write_bronze_pull`/`update_sync_state`), then passes the records through unchanged. | same |
| `silver_frames` | Flattens raw records into typed DataFrames via Phase 1's `flatten_*` functions. | same |
| `gold_tables` | Validates with Pandera and idempotently upserts into DuckDB via Phase 1's `load_silver_frames`. | same |
| `mart_daily_features` | The dbt model computing ML features on top of `daily_summary`. Represented as a real Dagster asset (via `dagster-dbt`'s `@dbt_assets`), not a subprocess call hidden from the graph. | `src/whoop_pipeline/orchestration/dbt_assets.py`, `dbt/models/marts/mart_daily_features.sql` |

The dbt project's five sources (`cycles`, `recovery`, `sleep`, `workouts`, `daily_summary`) are
mapped to the single upstream `gold_tables` asset via each source table's `meta.dagster.asset_key`
in `dbt/models/staging/sources.yml`, so Dagster's lineage correctly shows the dbt layer
depending on the Python-produced gold tables.

## The WHOOP data source is swappable, safely

`raw_whoop_data` takes a `whoop` resource typed as the `WhoopDataSource` protocol
(`src/whoop_pipeline/orchestration/resources.py`):

- `FixtureWhoopResource` returns Phase 1's static `tests/fixtures/*.json`, ignoring the
  requested date window. This is the **default**.
- `LiveWhoopResource` wraps the real, tested `WhoopClient`.

**The default is controlled by `WHOOP_PIPELINE_USE_LIVE_CLIENT`, not by whether
`WHOOP_ACCESS_TOKEN` happens to be set.** This is deliberate, not a stylistic choice: Dagster's
CLI auto-loads a `.env` file from the current working directory, and this repo has a real one
(for the pre-existing notebooks/manual workflow) containing a real access token. An earlier
version of this code chose the live resource whenever `WHOOP_ACCESS_TOKEN` was present in the
environment -- which meant a plain `dagster job execute` run from this directory silently
picked up the real `.env` and attempted a live call to `api.prod.whoop.com`. It only failed
because of an unrelated local network/SSL issue, not because of any safeguard in the code. Set
`WHOOP_PIPELINE_USE_LIVE_CLIENT=true` explicitly (only the scheduled CI workflow does this) to
opt into the live client; nothing else does.

`data_dir`/`database_path` are likewise injected via a `PipelinePathsResource`, defaulting to
Phase 1's real `data/` layout but overridable (both tests and the pytest materialization test
redirect these to a tmp_path).

## Running locally with `dagster dev`

```bash
pip install -e ".[dev]"
dagster dev
```

With no `WHOOP_ACCESS_TOKEN` or `WHOOP_PIPELINE_USE_LIVE_CLIENT` set, materializing the full
asset graph in the Dagster UI uses `FixtureWhoopResource` and completes with zero credentials,
writing to the real `data/` directory by default (override `paths` resource config in the UI to
redirect elsewhere). `dagster dev` picks up the `[tool.dagster] module_name` entry in
`pyproject.toml` automatically.

To run the same graph once, non-interactively:

```bash
dagster job execute -j whoop_pipeline_job -m whoop_pipeline.orchestration.definitions
```

## Running the dbt project directly

```bash
export WHOOP_DUCKDB_PATH=/absolute/path/to/data/processed/whoop.db
dbt build --project-dir dbt --profiles-dir dbt
```

`WHOOP_DUCKDB_PATH` defaults to `../data/processed/whoop.db` (relative to the dbt project
directory) if unset -- see `dbt/profiles.yml`. `tests/test_dbt_mart.py` overrides it to a
tmp_path database built from Phase 1's fixtures, so dbt is never run against real data in CI.

`accepted_range` is a small custom generic test in `dbt/tests/generic/`, not the `dbt_utils`
package -- this keeps the dbt project's own dependencies at zero, so `dbt build` never needs
network access to a package registry.

## Docker

```bash
docker build -t whoop-pipeline .
docker run --rm whoop-pipeline                                    # uses fixtures, no creds needed
docker run --rm -e WHOOP_PIPELINE_USE_LIVE_CLIENT=true \
  -e WHOOP_ACCESS_TOKEN=... -e WHOOP_CLIENT_ID=... \
  -e WHOOP_CLIENT_SECRET=... -e WHOOP_REFRESH_TOKEN=... \
  whoop-pipeline                                                  # real pull
```

The image installs the package non-editably (`pip install .`), copies `dbt/` separately since
it isn't part of the wheel, and pre-generates the dbt manifest at build time (`dbt parse`) so
the container never needs `dagster dev`'s manifest-regeneration behavior. It runs
`dagster job execute` once and exits -- there's no persistent daemon or webserver, since an
always-on host is out of scope for a personal project.

**Not verified with a literal `docker build`:** Docker isn't installed on the machine this was
built on. Every command the Dockerfile runs (`pip install .` non-editably, `dbt parse`,
`dagster job execute -j whoop_pipeline_job`) was verified working end-to-end outside a
container, from a clean directory with no `.env` present, using the fixture resource. The
Dockerfile itself should be verified with a real `docker build` before relying on it.

## Scheduled CI

`.github/workflows/scheduled-pipeline.yml` is separate from `ci.yml` (which stays push/PR-only,
lint+test, no secrets, and is untouched by this change). It builds the Docker image and runs it
with four secrets (`WHOOP_CLIENT_ID`, `WHOOP_CLIENT_SECRET`, `WHOOP_ACCESS_TOKEN`,
`WHOOP_REFRESH_TOKEN`) plus `WHOOP_PIPELINE_USE_LIVE_CLIENT=true`. It **cannot succeed yet** --
those secrets don't exist in the repo. See `NEXT_STEPS_FOR_HUMAN.md` for how to add them.

Two characteristics of `schedule:` triggers worth knowing, not bugs:
- GitHub's cron scheduling is best-effort and can be delayed under load.
- Scheduled workflows on public repos are automatically disabled after 60 days with no
  repository push activity (pushing anything re-enables them).

Also worth knowing: the container has no persistent volume. Each scheduled run starts from an
empty `data/` directory inside the ephemeral Actions runner, so incremental sync state does not
carry over between scheduled runs as written today -- every run re-pulls the initial
`--days-back` window rather than a true incremental sync. Giving the scheduled job real
persistence (a volume, cache, or the eventual Postgres/object-storage serving layer) is
follow-up work, not solved by this session.
