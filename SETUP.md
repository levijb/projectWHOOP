# Setup and deployment

projectWHOOP runs locally with fixtures and DuckDB, or with a WHOOP account and a private
Postgres database. Production uses one account and one writer. Supabase ingestion and
duplicate-free reruns are validated; repeat the checks below for each deployment environment.
Docker and scheduled execution require their own verification. Model training remains a
separate, deliberate activation step.

## Install and verify locally

Use Python 3.11 or newer from the repository root. In PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
pytest --cov=whoop_pipeline --cov-report=term-missing
```

On macOS/Linux, activate with `source .venv/bin/activate`. The tests require no credentials
and block live WHOOP/Postgres connections. See [the development guide](CONTRIBUTING.md) for
the local-only notes convention and [orchestration](docs/orchestration.md) for optional
embedded PostgreSQL tests.

For a fixture pipeline, explicitly disable both live paths and isolate its output:

```powershell
$env:WHOOP_PIPELINE_USE_LIVE_CLIENT = "false"
$env:WHOOP_PIPELINE_USE_POSTGRES = "false"
$env:WHOOP_PIPELINE_DATA_DIR = "data/fixture_dev"
$env:WHOOP_DUCKDB_PATH = (Join-Path (Get-Location) "data/fixture_dev/processed/whoop.db")
dagster job execute -j whoop_pipeline_job -m whoop_pipeline.orchestration.definitions
```

Use `dagster dev` for the local UI. After gold tables exist, `whoop-dbt build --target dev`
and `whoop-dbt docs generate --target dev` operate on the same DuckDB path.

## Configure WHOOP credentials

1. Register an application in the [WHOOP developer portal](https://developer.whoop.com/) with
   redirect URI `http://localhost:3000/callback`.
2. Copy `.env.example` to `.env` **only if `.env` does not already exist**. Set the client ID
   and client secret locally. Keep both pipeline opt-in flags false/unset for normal development.
3. Run `python scripts/authenticate.py` manually, complete browser authorization, and securely
   save the returned access **and refresh** tokens in `.env`. This command contacts WHOOP and
   prints sensitive tokens; do not share its output or commit it.

For an explicit local bronze download, run:

```powershell
python scripts/download_whoop_data.py --days-back 180
```

This direct downloader contacts WHOOP using the supplied access token; it is not controlled
by the Dagster live-client flag and does not populate Postgres or automatically rotate tokens.
It saves JSONL and the local checkpoint under `data/`. Later downloads overlap the last
successful UTC date. Keep fixture output separate from real data.

For persistent production pulls, use the Dagster/Postgres path below. After bootstrap, stored
Postgres tokens are authoritative. Do not run the authentication helper routinely or share
the refresh token with another process that may rotate it.

## Configure and migrate Postgres

Set `DATABASE_URL` to a PostgreSQL connection string, not a Supabase HTTPS project URL.
Supported schemes are `postgres://`, `postgresql://`, and `postgresql+psycopg2://`. URL-encode
special characters in passwords. The client defaults to `sslmode=require` and a 10-second
connection timeout; supported query options are `sslmode`, `sslrootcert`, and `connect_timeout`.
Use `sslmode=verify-full` with a suitable CA file for server identity verification. Choose a
provider connection/pooler method compatible with the host and retain TLS.

Keep credentials out of commits and shared terminal output. Rotate any exposed password and
update the local URL and Actions secret. If an outdated `DATABASE_URL` remains in the shell,
clear it before using dotenv's `--no-override` option, which preserves shell values.

**The following commands contact the database.** Use a role with schema/table creation rights:

```powershell
$env:WHOOP_PIPELINE_USE_POSTGRES = "true"
$env:WHOOP_PIPELINE_USE_LIVE_CLIENT = "false"
python -m dotenv -f .env run --no-override alembic upgrade head
python -m dotenv -f .env run --no-override alembic current
```

Alembic creates the private `whoop` schema and version tracker. Revision `0001` provisions
gold/state/token storage and the daily view; `0002` adds prediction serving. Existing installs
at `0001` need only the normal upgrade to `head`. Applied revisions are not rerun. Alembic
does not implicitly load `.env`; the explicit dotenv wrapper supplies it for each command.

Do not expose `whoop` in Supabase's Data API or grant access to anonymous/authenticated API
roles. Review conflicting schemas before migrating; do not delete them blindly. Never use
`stamp`, `downgrade`, or schema deletion as a routine migration workaround. Use new Alembic
revisions for schema changes and protect database backups, including stored tokens.

## Validate persistent ingestion

Stop competing local jobs, notebooks, and scheduled token refreshers first. **Both live
opt-ins must be true before materializing production**; Postgres with the fixture client
writes synthetic IDs and is appropriate only for a separate disposable test database.

```powershell
$env:WHOOP_PIPELINE_USE_LIVE_CLIENT = "true"
$env:WHOOP_PIPELINE_USE_POSTGRES = "true"
python -m dotenv -f .env run --no-override dagster job execute -j whoop_pipeline_job -m whoop_pipeline.orchestration.definitions
```

This contacts WHOOP and Postgres. The initial backfill is 180 days; later runs overlap the
stored checkpoint. The first token refresh requires client ID/secret and a valid bootstrap
refresh token; a bootstrap access token is optional. Refreshed tokens persist before ingestion.
Existing local DuckDB history is not automatically migrated into Postgres.

Check counts and checkpoints in the database SQL editor without selecting token values:

```sql
SELECT 'cycles' AS relation, count(*) AS rows, count(DISTINCT cycle_id) AS distinct_ids
FROM whoop.cycles
UNION ALL
SELECT 'recovery', count(*), count(DISTINCT cycle_id) FROM whoop.recovery
UNION ALL
SELECT 'sleep', count(*), count(DISTINCT sleep_id) FROM whoop.sleep
UNION ALL
SELECT 'workouts', count(*), count(DISTINCT workout_id) FROM whoop.workouts
UNION ALL
SELECT 'mart_daily_features', count(*), count(DISTINCT cycle_id) FROM whoop.mart_daily_features;

SELECT count(*) AS daily_rows FROM whoop.daily_summary;
SELECT last_synced_date FROM whoop.sync_state WHERE id = 1;
SELECT count(*) AS stored_pairs, min(expires_at) AS token_expires_at FROM whoop.whoop_tokens;
```

Expect one checkpoint/token pair, equal row/distinct-ID counts, and one mart/daily-summary row
per cycle. An account with data in the backfill window should have nonzero counts. Confirm
dbt uses `prod` and its source/mart tests pass. Repeat the Dagster command in a new process:
history must persist without duplicate IDs, although newly available/corrected records may
change counts. On a later run after natural token expiry, verify the stored expiry advances;
do not edit production token values or timestamps to force a refresh.

If gold commits but dbt fails, rerun the job or `whoop-dbt build --target prod` with the
Postgres opt-in and URL supplied. A failure after WHOOP rotates tokens but before database
persistence may require reauthorization and deliberate replacement of the stale stored pair.
Changing bootstrap secrets alone does not override stored tokens.

Reset both flags to `false` before fixture development. Gold/checkpoint writes are atomic,
but raw bronze and Dagster run state remain ephemeral in scheduled containers. Local tests
do not establish provider permissions, TLS/pooler compatibility, or live token behavior.

## Verify Docker and enable GitHub Actions

With Docker installed, verify the credential-free image first:

```bash
docker build -t whoop-pipeline .
docker run --rm whoop-pipeline
```

The fixture run must succeed without secrets. Local Python checks are not a substitute for
testing the Linux image and its dependency resolution. The image is a one-shot ingestion job;
it does not run model training or a persistent server.

After the database and image checks pass, configure repository Actions secrets:

| Secret | Purpose |
|---|---|
| `DATABASE_URL` | Tested TLS-enabled Postgres connection string |
| `WHOOP_PIPELINE_USE_POSTGRES` | Exactly `true`; prevents ephemeral-storage fallback |
| `WHOOP_CLIENT_ID` | Client identity for token refresh |
| `WHOOP_CLIENT_SECRET` | Client secret for token refresh |
| `WHOOP_REFRESH_TOKEN` | Initial bootstrap; stored tokens take precedence afterward |
| `WHOOP_ACCESS_TOKEN` | Optional bootstrap/local-live compatibility token |

The workflow sets `WHOOP_PIPELINE_USE_LIVE_CLIENT=true`. Rotated tokens stay in Postgres;
they are not written back to GitHub secrets. Never upload data, token query results, or
unredacted logs as workflow artifacts.

Manually dispatch **Scheduled WHOOP pipeline**, verify persistence/checkpoints, then rely on
its daily 06:00 UTC schedule. GitHub schedules are best-effort and may be delayed or disabled
after public-repository inactivity. Workflow concurrency protects Actions runs only; do not
overlap them with local production jobs.

## Model readiness

Keep model updates disabled while history is sparse. Synthetic models are software validation,
not personal forecasts. The guard requires **90 labeled next-cycle pairs spanning at least
90 days**; aim for **120-180+ days**, class diversity, representative behavior, and acceptable
missingness before interpreting results. Review genuine walk-forward metrics, compare a
persistence baseline, check interval coverage, and collect prospective errors at a consistent
forecast time. Historical features can be revised after the fact.

Only after review, manually enable both modeling resource switches and run the separate
model-update job after ingestion/dbt, retaining the Postgres opt-in. Use one persistent local
MLflow registry and one writer. Do not automatically promote challengers or add modeling to
Actions. Commands, model contracts, and local `mlflow ui` instructions are in
[docs/modeling.md](docs/modeling.md).

## Notebook compatibility

`WHOOP_EDA.ipynb` still depends on `src/whoop_client.py` and `src/whoop_oauth.py`. Before removing
those legacy helpers, migrate its imports to `whoop_pipeline.client.WhoopClient` and
`whoop_pipeline.oauth.get_whoop_access_token`, adapting constructor/return shapes. The two data
explorer notebooks do not depend on those helpers. Notebook migration is separate from setup;
keep personal data and executed outputs out of commits.
