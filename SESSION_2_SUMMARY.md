# Phase 2 session summary

## Built

- A dbt project (`dbt/`) declaring `cycles`, `recovery`, `sleep`, `workouts`, and
  `daily_summary` as sources (no bronze/silver/gold logic reimplemented) and a
  `mart_daily_features` mart with rolling averages, a lag, day-of-week, and rest-day recency.
  Tests on both sources and the mart (`not_null`, `unique`, a custom `accepted_range` generic
  test, `relationships` back to `daily_summary`). `dbt docs generate` builds cleanly.
- A pytest integration test (`tests/test_dbt_mart.py`) that builds a throwaway DuckDB from
  Phase 1's fixtures and runs the real dbt project against it via subprocess.
- Dagster software-defined assets (`src/whoop_pipeline/orchestration/`) wrapping the existing,
  tested Phase 1 functions: `raw_whoop_data -> bronze_partitions -> silver_frames ->
  gold_tables -> mart_daily_features` (the last via `dagster-dbt`'s `@dbt_assets`, so dbt is a
  first-class part of the lineage). A swappable `WhoopDataSource` resource (fixture-backed
  default, live client behind an explicit opt-in flag — see Judgment calls) and a
  `PipelinePathsResource` so tests never touch the real `data/` directory.
- A pytest test materializing the full asset graph in-process against fixtures with zero
  credentials (`tests/test_orchestration.py`), plus a regression test pinning the safe resource
  default (see below).
- A `Dockerfile` for a one-shot `dagster job execute` run (not a persistent daemon).
- `.github/workflows/scheduled-pipeline.yml`, a daily-cron workflow separate from the existing
  push/PR `ci.yml`, building and running the Docker image against four WHOOP secrets.
- `docs/orchestration.md` and README updates describing the whole graph.

## A real safety issue found and fixed during this session

While verifying the Dagster job could run via `dagster job execute`, a **live network call to
`api.prod.whoop.com` was actually attempted**, using a real access token. Root cause: this
repo's real `.env` (pre-existing, for unrelated manual/notebook use) contains a real
`WHOOP_ACCESS_TOKEN`, and Dagster's CLI auto-loads `.env` from the working directory. My first
draft of the resource-selection logic chose the live WHOOP client whenever `WHOOP_ACCESS_TOKEN`
was present in the environment — which meant simply running `dagster job execute` from this
directory silently picked up real credentials and tried a real API call. It only failed because
of an unrelated local SSL/network issue on this machine, not because of anything in the code.

Fixed by decoupling the live/fixture choice from credential presence entirely: the resource now
defaults to fixtures unless a separate, explicit `WHOOP_PIPELINE_USE_LIVE_CLIENT` flag is set.
Added `tests/test_orchestration.py::test_definitions_default_to_fixture_resource_without_explicit_opt_in`
as a permanent regression test — it sets a fake `WHOOP_ACCESS_TOKEN` and asserts the fixture
resource is still chosen. All subsequent verification in this session was done either via
direct Python import (no subprocess, no `.env` auto-loading) or from a scratch directory with
no `.env` present at all, mirroring exactly what the Docker container will see. No real WHOOP
data was ever exchanged — the connection failed at the TLS handshake, before any request
reached the server.

## Judgment calls

- **`WHOOP_PIPELINE_USE_LIVE_CLIENT` as a separate opt-in flag**, not inferred from
  `WHOOP_ACCESS_TOKEN` presence. Directly required by the safety issue above — the two must not
  be conflated in this repo, which already has a real `.env` for unrelated purposes.
- **`mart_daily_features` feature set**: 7-cycle rolling averages of recovery score, strain, and
  HRV (using the 7 most recent cycles rather than a strict 7-calendar-day window, since cycles
  are normally ~1/day and this avoids needing to fill date gaps); `prior_day_strain` as a
  one-cycle lag; `day_of_week`; a rolling sleep-debt average plus today's deviation from it
  (`sleep_debt_trend_hours`); and `days_since_last_low_strain_day`, using a strain threshold of
  8.0 on WHOOP's 0–21 strain scale to mean "rest day." The threshold is a reasonable but
  arbitrary judgment call — Phase 3 modeling may want to tune or replace it.
- **`accepted_range` implemented as a local custom generic dbt test**, not the `dbt_utils`
  package. Keeps the dbt project's dependencies at zero, so `dbt build`/`dbt deps` never needs
  network access — consistent with this project's offline-verifiable philosophy and this
  machine's own known git/SSL access problems with GitHub.
- **dbt sources mapped to the `gold_tables` Dagster asset via `meta.dagster.asset_key`** in
  `sources.yml`, rather than a custom `DagsterDbtTranslator` subclass. Simpler, and it's dbt's
  own documented mechanism for this exact purpose.
- **`raw_whoop_data`/`bronze_partitions`/`gold_tables` take a `PipelinePathsResource`** instead
  of using Phase 1's hardcoded `DEFAULT_DATA_DIR`/`DEFAULT_DATABASE_PATH` constants directly.
  Without this, the in-process materialization test would have written into the real repo's
  `data/` directory. Defaults still match Phase 1's production paths.
- **No `from __future__ import annotations` in `assets.py`/`dbt_assets.py`.** Dagster
  1.13.20's op/asset decorators reject a stringified forward reference on the `context`
  parameter (`DagsterInvalidDefinitionError`); every other module in this codebase keeps the
  future import, these two deliberately don't.
- **Dagster/dbt-core/dbt-duckdb/dagster-dbt versions**: pinned to what actually resolved and
  was tested (`dagster` 1.13.20, `dagster-dbt` 0.29.20, `dbt-core` 1.10.23, `dbt-duckdb`
  1.10.1), inside the range dagster-dbt documents supporting (dbt-core 1.7–1.11). dbt-core
  1.10.x prints its own upstream deprecation notice ("no longer receives regular patches") —
  a known characteristic of that dbt release, not a bug introduced here.
- **Docker image includes `tests/fixtures/`** so it can be verified/run with zero credentials
  (the default resource); real scheduled runs set `WHOOP_PIPELINE_USE_LIVE_CLIENT=true` plus
  the WHOOP secrets and never touch the fixtures.

## Known limitations, disclosed rather than silently accepted

- **The Dockerfile was never actually built.** Docker isn't installed on the machine this
  session ran on. Every command it runs was verified working end-to-end outside a container
  (non-editable `pip install .`, `dbt parse` for manifest pre-generation, and
  `dagster job execute -j whoop_pipeline_job`, the last run from a clean directory with no
  `.env` present to mirror the container exactly) — but `docker build` itself needs to be run
  for real before the image can be trusted.
- **The scheduled workflow cannot succeed yet** — the four WHOOP secrets don't exist in the
  repo. Expected, per this session's brief; see `NEXT_STEPS_FOR_HUMAN.md`.
- **No token refresh.** The live resource uses `WHOOP_ACCESS_TOKEN` as-is; it doesn't exchange
  `WHOOP_REFRESH_TOKEN` for a new access token when the current one expires. A truly unattended
  daily schedule needs this eventually — out of scope for this session.
- **No persistence between scheduled runs.** The container has no volume; each scheduled run
  starts from an empty `data/` directory in the ephemeral Actions runner, so incremental sync
  state doesn't carry over as written today. Documented in `docs/orchestration.md`, not solved.
- **Postgres is intentionally absent**, per this session's brief (§0) — deferred until a
  serving layer actually needs one, likely Phase 3.

## Verification

`ruff check .`, `ruff format --check .`, `mypy`, and `pytest` (34 tests: the 31 from Phase 1 plus
3 new: the dbt integration test, the full-graph materialization test, and the resource-default
regression test) were run repeatedly through this session and are all clean at the end of it.
`ci.yml` (push/PR, no secrets) is untouched and still passes with the same commands it always
ran, now covering the new orchestration code too.

## Next phase

Phase 3: the ML recovery-prediction modeling suite (per `Recovery_Prediciton/README.md`),
consuming `mart_daily_features` as its feature source. Likely also where Postgres and MLflow
finally earn their place, and where WHOOP token-lifecycle/refresh handling should be built if
the scheduled pipeline is to run unattended for real.
