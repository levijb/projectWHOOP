# Orchestration and persistent storage

Phase 1 supplies the client, bronze files, transforms, validation, and DuckDB loader. Phase 2
adds dbt and Dagster. Phase 3 adds durable Postgres storage and unattended token refresh.
Phase 4 adds the ML modeling suite consuming `mart_daily_features`, local MLflow versioning,
and `whoop.predictions` (Alembic 0002). See [modeling.md](modeling.md) for synthetic experiments
and deliberate activation. Model updates remain disabled until sufficient history is available.

## Asset graph

```text
raw_whoop_data -> bronze_partitions -> silver_frames -> gold_tables -> mart_daily_features
                                                                        |
                                                                        v
                                                    daily_model_update (disabled by default)
```

| Asset | Behavior |
|---|---|
| `daily_model_update` | Separate opt-in job after the feature mart: settle actuals, apply retrain triggers, update/version SGD, persist next-cycle recovery with interval. Never selected by the existing scheduled ingestion job. |
| `raw_whoop_data` | Reads the backend's checkpoint and fetches four collections through the injected `whoop` resource. The first run requests 180 days; subsequent runs overlap the last successful UTC date. |
| `bronze_partitions` | Writes atomic local JSONL partitions. Does **not** advance the checkpoint. |
| `silver_frames` | Flattens the four collections and carries the pull date forward. |
| `gold_tables` | Validates every frame, replaces matching IDs, and records the checkpoint only after successful gold writes. |
| `mart_daily_features` | Runs `dbt build` through `dagster-dbt`, retaining model lineage and dbt tests. |

The five dbt sources map to `gold_tables` through `meta.dagster.asset_key`. Python owns the
gold tables and daily-summary view; dbt owns only derived features. Its existing 7-cycle
windows (not strictly calendar days), lag, sleep-debt trend, and rest-day recency remain.
Postgres uses `extract(dow ... at time zone 'UTC')`; a running `max` of the monotonically
increasing rest-day index replaces DuckDB-only `IGNORE NULLS`.

## Two independent, explicit opt-ins

| WHOOP live flag | Postgres flag | Source | Gold/checkpoint | dbt |
|---|---|---|---|---|
| unset/false | unset/false | fixtures | local DuckDB + JSON | dev |
| true | unset/false | live, supplied access token | local DuckDB + JSON | dev |
| unset/false | true | fixtures | Postgres | prod |
| true | true | live, persistent rotating tokens | Postgres | prod |

Flags are `WHOOP_PIPELINE_USE_LIVE_CLIENT` and `WHOOP_PIPELINE_USE_POSTGRES`. Each recognizes
`true`, `1`, or `yes`, case-insensitively. Postgres also requires `DATABASE_URL`.
**Credentials or a URL alone never enable either path.** Both original resource-default
regressions are retained and extended. A Postgres-only run intentionally writes fixtures;
do not use that combination against your production database.

Dagster CLI auto-loads the working directory's `.env`. The live resource itself does not
load dotenv. URL fields use Dagster `EnvVar` references so resource configuration does not
embed the password. Default/import-time dbt manifest generation explicitly uses `dev`;
only the opted-in execution resource selects `prod`.

## Storage and migration contract

`GoldStorageBackend` is the small injected protocol. `LocalBackend` delegates to Phase 1's
DuckDB loader and JSON state helpers. `PostgresBackend` delegates to SQLAlchemy Core.

Postgres gold and checkpoint writes share one transaction. Validation precedes engine
creation, and inserts/deletes are chunked in groups of 500 records. The key behavior matches
DuckDB: incoming IDs replace existing rows; unrelated history is retained. Runtime writes
use declared SQLAlchemy tables, never pandas' implicit table creation.

Local DuckDB commits before its JSON checkpoint is updated. A crash between these operations
causes a safe replay, not skipped gold. A dbt failure does not undo committed gold; the next
run rebuilds the complete mart. Bronze files and Dagster intermediates are still ephemeral in
the scheduled container; **Postgres persists gold, sync state, current tokens, and the mart,
not the raw archive**.

Alembic revision `0001` creates the private `whoop` schema, four gold tables, `sync_state`,
`whoop_tokens`, and `daily_summary`. Cycle/user identifiers use 64-bit integers. The
checkpoint is a SQL date; timestamps use timezone-aware Postgres types. The migration's
daily view matches Phase 1's latest main-sleep and interval-assigned workout policy.
The version tracker is `whoop_alembic_version` in the connection's default schema.

No migration runs on import or as an ingestion side effect. Explicitly run
`alembic upgrade head` before first use and when new revisions ship. The Alembic CLI also
requires the Postgres flag plus URL; even ambient credentials cannot trigger it accidentally.
URL objects avoid percent-password interpolation problems in Alembic configuration.
Do not edit applied revisions.

The `whoop` schema revokes access from `PUBLIC`. Use an owner/migration role initially,
or provision a restricted pipeline role with the necessary schema/table privileges afterward.
Do not expose this schema in Supabase's Data API or grant it to `anon`/`authenticated`.
Tokens are stored as database text: private schema access, protected backups, and database
credentials are essential. This is not application-level encryption.

## Token lifecycle

Only the live **and** Postgres combination automatically refreshes tokens:

1. Read the stored access/refresh pair and expiry, preferring it over bootstrap environment values.
2. If there is no stored pair, treat bootstrap expiry as unknown and refresh immediately.
   Only the client ID, client secret, and refresh token are needed for this first refresh.
3. Reuse a stored pair more than five minutes from expiry. Otherwise refresh with
   `scope=offline`, validate both rotated tokens and a positive finite `expires_in`,
   then persist the pair and absolute UTC expiry **before** fetching collections.
4. On refresh failure, do not overwrite the pair or fall back to stale bootstrap secrets.

The local live-only path retains the supplied access-token behavior; it never rotates an
unpersisted refresh token. The fixture path reads no credentials. The human-only
`scripts/authenticate.py` now returns both bootstrap tokens and their expiry, but is never
invoked by scheduled jobs or tests.

WHOOP invalidates the prior tokens on refresh, so the workflow serializes runs with
`concurrency` and `cancel-in-progress: false`. This is a **single-account, single-writer**
pipeline. Do not overlap local production runs with Actions or share its refresh token with
another refresher. There is no distributed refresh lock for unrelated processes.

The WHOOP exchange and database save cannot be one atomic transaction. A crash or database
failure after rotation but before persistence can require human reauthorization. No automatic
retry of an ambiguous exchange or API 401 is performed. Requests that run beyond the token's
remaining lifetime can also fail; the next run safely resumes from the checkpoint.

## Safe local execution

Install with `python -m pip install -e ".[dev]"`. From the project root, in PowerShell:

```powershell
$env:WHOOP_PIPELINE_USE_LIVE_CLIENT = "false"
$env:WHOOP_PIPELINE_USE_POSTGRES = "false"
$env:WHOOP_PIPELINE_DATA_DIR = "data/fixture_dev"
$env:WHOOP_DUCKDB_PATH = (Join-Path (Get-Location) "data/fixture_dev/processed/whoop.db")
dagster dev
```

Materialize the full graph in the UI, or run once:

```powershell
dagster job execute -j whoop_pipeline_job -m whoop_pipeline.orchestration.definitions
```

The explicit scratch paths prevent fixture runs from mixing with your real local data.
For direct dbt work, after building fixture gold:

```powershell
whoop-dbt build --target dev
whoop-dbt docs generate --target dev
```

The original `dbt build --project-dir dbt --profiles-dir dbt --target dev` remains supported.
For production, use `whoop-dbt build --target prod` after [setup](../SETUP.md). This
wrapper derives host, decoded user/password, database, port, and TLS options from
`DATABASE_URL` in the child environment. It never prints shell exports or writes a password
file. `scripts/parse_database_url_for_dbt.py` is a compatibility entry point for that wrapper,
not an export generator.

Both SQLAlchemy and dbt default to `sslmode=require` and a 10-second connection timeout.
Supported URL options are `sslmode`, `sslrootcert`, and `connect_timeout`; unsupported
options fail instead of silently making the two clients use different settings.
Use `verify-full` plus an appropriate CA for server identity verification.
The dbt password variable is prefixed `DBT_ENV_SECRET_` for dbt's log scrubbing.

## Docker and schedule

```bash
docker build -t whoop-pipeline .
docker run --rm whoop-pipeline
```

The package is installed non-editably. The image includes its README/license build inputs,
dbt project, Alembic revisions, and fixtures. It pre-parses the safe dev manifest and executes
one Dagster job. `.dockerignore` excludes dotenv files, local wearable data, Git history,
virtual environments, and generated artifacts. The build takes no production credentials.

After the human migration/smoke test and with credentials already in the caller's environment:

```bash
docker run --rm \
  -e WHOOP_PIPELINE_USE_LIVE_CLIENT=true -e WHOOP_PIPELINE_USE_POSTGRES=true \
  -e DATABASE_URL -e WHOOP_CLIENT_ID -e WHOOP_CLIENT_SECRET \
  -e WHOOP_ACCESS_TOKEN -e WHOOP_REFRESH_TOKEN whoop-pipeline
```

The separate scheduled workflow runs daily at 06:00 UTC. It requires the
`WHOOP_PIPELINE_USE_POSTGRES` repository secret to equal `true`, along with
`DATABASE_URL` and WHOOP credentials. Missing configuration fails before the container can
fall back to local storage. `ci.yml` remains unchanged and needs no secrets.

GitHub schedules can be delayed and public-repository schedules can be disabled after 60
days of inactivity. See [SETUP.md](../SETUP.md) for activation and smoke-test steps.

## Verification boundaries

Tests migrate temporary SQLite files with **the real Alembic revision**, not
`metadata.create_all`. They cover idempotency, corrected/partial data, nullable values,
restart persistence, transaction rollback, checkpoint failures, token rotation, and resource
selection. SQLite does not implement the Postgres daily view or verify its permissions,
TLS, locking, or pooler behavior.

The actual dbt `prod` model compiles with `psycopg2.connect` blocked. Its SQL and the
migration's Postgres daily view also execute on fixture DuckDB for a semantic comparison.
The complete local Dagster/dbt graph is exercised against temporary data. Tests block external
Python sockets and native psycopg2 connections; Windows asyncio loopback remains allowed.
The suite strips inherited credentials before collection and disables dbt telemetry.

### Postgres SQL review and embedded-engine regression

Postgres rejects a final `USING (cycle_id)` when a preceding sleep `JOIN ... ON` leaves both
`c.cycle_id` and `s.cycle_id` on the join's left side. DuckDB permits that structure, so both
view definitions explicitly join workout aggregates with `ON c.cycle_id = w.cycle_id`.
Workouts have no stored `cycle_id`;
the temporal assignment remains authoritative.

When reviewing raw Postgres migrations or dbt's `prod` SQL:

- Check join-column scope after every join. Prefer an explicit qualified key when a joined
  relation can contain duplicate names; a later `USING` does not know which alias you intend.
- Enumerate workout CTE columns alongside its computed cycle ID instead of forwarding `w.*`.
- Do not carry DuckDB-only syntax such as `* EXCLUDE (...)`, `dayofweek(...)`, or
  `last_value(... IGNORE NULLS)` into Postgres SQL. Review the rendered production SQL.
- Verify on a PostgreSQL engine when possible. Successful parsing/execution in DuckDB or
  SQLite does not establish PostgreSQL compatibility.

`tests/test_postgres_runtime.py` adds an optional PostgreSQL-engine check using
[PGlite](https://pglite.dev/docs/), a local in-memory WASM runtime. It runs the complete emitted
Alembic migration, inserts fixture-derived rows, and compares all view columns and values with
DuckDB. Cases include missing main sleep, overlapping/open-ended cycles, a cycle with no
workouts, exact interval boundaries, gaps, and unmatched workouts. Node sockets and fetch are
blocked; neither `.env` nor any live database is used.

To enable it in PowerShell with Node installed, install the test-only package in a temporary
directory (not the application or Docker image):

```powershell
$env:PROJECTWHOOP_PGLITE_DIR = Join-Path $env:TEMP ("whoop-pglite-" + [guid]::NewGuid())
npm install --prefix $env:PROJECTWHOOP_PGLITE_DIR --ignore-scripts --no-audit --no-fund @electric-sql/pglite@0.5.8
python -m pytest -q tests/test_postgres_runtime.py
```

With that variable set, the full `pytest` suite includes these three cases. Without it, the
existing Python-only CI still runs normally and explicitly skips the three optional cases.
Installation uses the npm registry; test execution is offline. If npm needs access to a
system-installed CA, configure Node's `--use-system-ca` option rather than disabling TLS
verification. Provider permissions, TLS/pooler behavior, and Docker require environment-specific
checks; see [SETUP.md](../SETUP.md).

A non-editable wheel and the one-shot Dagster/dbt commands are additionally checked from a
scratch directory without `.env`. **No live Postgres or WHOOP connection is part of this
verification. Verify Docker separately before deployment.**

Implementation references:
[WHOOP OAuth and rotation](https://developer.whoop.com/docs/developing/oauth/),
[dbt Postgres profiles](https://docs.getdbt.com/docs/local/connect-data-platform/postgres-setup),
[dbt secret environment variables](https://docs.getdbt.com/reference/dbt-jinja-functions/env_var),
[Dagster dbt integration](https://docs.dagster.io/integrations/libraries/dbt/dagster-dbt).
