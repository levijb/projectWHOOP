# Manual steps before a real WHOOP pull

1. Create a WHOOP developer application at `developer.whoop.com` and register
   `http://localhost:3000/callback` as its redirect URI.
2. Copy `.env.example` to `.env` and enter the application client ID and client secret locally.
   Do not commit `.env`.
3. Run `python scripts/authenticate.py` once on your own machine, complete the browser login, and
   place the returned access/refresh tokens in the local `.env`.
4. Run `python scripts/download_whoop_data.py --days-back 180` to perform the first real pull.

## Notebook migration (retire the old client)

`src/whoop_client.py` and `src/whoop_oauth.py` are the pre-Phase-1 client and OAuth helpers.
They were kept rather than deleted during the Phase 1 merge because
`notebooks/WHOOP_EDA.ipynb` still imports them directly (`from src.whoop_client import
WhoopClient`, `from src.whoop_oauth import get_whoop_access_token`, in multiple cells).
`notebooks/WHOOP_Data_Explorer.ipynb` and `notebooks/WHOOP_Data_Explorer_DuckDB.ipynb` do not
reference either module, so they're already clear.

To finish retiring the old client:
1. Update `WHOOP_EDA.ipynb` to use `whoop_pipeline.client.WhoopClient` and
   `whoop_pipeline.oauth.get_whoop_access_token` instead (constructor/return shapes differ
   slightly — check `src/whoop_pipeline/client.py` and `src/whoop_pipeline/oauth.py`).
2. Once no notebook imports `src.whoop_client` or `src.whoop_oauth`, delete both files (and
   their `__pycache__` entries).

This is a deliberate follow-up task, not something done automatically as part of the Phase 1
merge — rewriting a notebook's cells isn't a mechanical change.

## Phase 3: verify and enable persistent scheduled runs

The implementation is complete locally, but **the real database, Docker image, and hosted
schedule are not verified**. No secrets were added or workflow dispatched by the build session.
Phase 4 (ML) follows this operator verification.

Use the active Git checkout:
`C:\Users\levij\Documents\Personal\Code\projectWHOOP`.
The similarly named `Documents\GitHub\projectWHOOP` directory is an older Phase 1 copy.

### 1. Keep production credentials private

Your local `.env` already has a `DATABASE_URL` key. Its value was not inspected or used.
Confirm yourself that it is a Postgres connection string, not the Supabase HTTPS project URL.
Use your provider's connection method appropriate for this machine/Actions runner, URL-encode
special characters in the password, and retain TLS. Do not paste credentials into a task or
commit them.

The code supports `postgres://`, `postgresql://`, and `postgresql+psycopg2://`. It defaults
to `sslmode=require`; for certificate identity verification use `sslmode=verify-full` and
`sslrootcert` pointing to an available CA file. Only `sslmode`, `sslrootcert`, and
`connect_timeout` query options are supported. Check pooler/TLS compatibility in the smoke test.

### 2. Run the one-time real-Postgres smoke test yourself

**After the `daily_summary` ambiguity fix:** the never-applied `0001` migration has been
corrected in place. Rotate the database password exposed in the pasted terminal command and
update your local `DATABASE_URL` (and any Actions secret) before retrying. No database cleanup,
`stamp`, `downgrade`, or migration bypass is required for the reported failed first migration.
Do not add implicit dotenv loading to Alembic; use the explicit dotenv runner below if the
updated URL is only in `.env`. If the old URL is still set in this PowerShell session, remove
that stale environment variable first so `--no-override` does not preserve it.

Your next database action is simply **rerun `alembic upgrade head`**, or its dotenv-wrapped
equivalent below, with `WHOOP_PIPELINE_USE_POSTGRES=true`. The fixture/Postgres combination
should only be used in a disposable test database, never to seed the production history.

**These commands deliberately contact your database and, in the second step, WHOOP. They
were not run during development.** Do not run a local production job concurrently with
Actions or another program refreshing the same token pair. Close any token-refreshing notebook.

From the active checkout in PowerShell, install/update the package and set the explicit flags.
The dotenv runner loads your existing file only for each child process; `--no-override`
preserves the flags explicitly set in your shell.

```powershell
python -m pip install -e ".[dev]"
$env:WHOOP_PIPELINE_USE_POSTGRES = "true"
$env:WHOOP_PIPELINE_USE_LIVE_CLIENT = "false"
python -m dotenv -f .env run --no-override alembic upgrade head
```

This creates the private `whoop` schema and Alembic version tracker. Use a database role with
schema/table creation rights. Do not enable this schema in Supabase's Data API or grant access
to anonymous/authenticated API roles. A pre-existing conflicting `whoop` schema needs human
review; do not delete it blindly. Future schema changes require new Alembic migrations.
Do not run `alembic downgrade` against your production history.

Now enable the live client **before** materializing, so synthetic fixture IDs are not inserted:

```powershell
$env:WHOOP_PIPELINE_USE_LIVE_CLIENT = "true"
python -m dotenv -f .env run --no-override dagster job execute -j whoop_pipeline_job -m whoop_pipeline.orchestration.definitions
```

On the first run, the backend exchanges your bootstrap refresh token and immediately persists
the rotated pair. A valid bootstrap refresh token plus client ID/secret are required; the
bootstrap access token is optional for this path. If reauthorization is needed, manually run
`python scripts/authenticate.py` and securely store its access **and refresh** tokens.
Do not run that helper routinely: stored Postgres tokens are authoritative after bootstrap.

In your database SQL editor, verify row counts/checkpoints without displaying token values:

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

Expect one checkpoint and one token pair, equal row/distinct-ID counts for each relation, and
one mart/daily-summary row per cycle. For an account with data in the backfill window, counts
should be nonzero. Run the same Dagster command again in a new process and confirm history is
retained without duplicate IDs. WHOOP may add/correct records between runs, so counts need
not be identical. Check that dbt's source/mart tests pass and its target is `prod`.

Also perform a later run after the token expires and confirm the stored expiry advances.
Do not edit tokens or expiry artificially in production merely to force a refresh.

When done, reset local defaults before any fixture development:

```powershell
$env:WHOOP_PIPELINE_USE_LIVE_CLIENT = "false"
$env:WHOOP_PIPELINE_USE_POSTGRES = "false"
```

Leave flags false/unset in the normal local `.env`. See
[docs/orchestration.md](docs/orchestration.md) for safe, separate fixture-data paths.

### 3. Verify the Docker image

Docker was not installed or used during this session. When available:

```bash
docker build -t whoop-pipeline .
docker run --rm whoop-pipeline
```

The default fixture run must succeed without credentials. The non-container wheel,
multiprocess Dagster job, dbt build, and dbt docs generation were checked separately; that
does not verify Docker's Linux image or its Python 3.11 dependency resolution.

### 4. Configure GitHub Actions only after the smoke test

Review/push the local commits, then add repository Actions secrets:

| Secret | Purpose |
|---|---|
| `DATABASE_URL` | The tested Postgres connection string |
| `WHOOP_PIPELINE_USE_POSTGRES` | Exactly `true`; workflow refuses to fall back to ephemeral local storage |
| `WHOOP_CLIENT_ID` | Required for future token refreshes |
| `WHOOP_CLIENT_SECRET` | Required for future token refreshes |
| `WHOOP_REFRESH_TOKEN` | Initial bootstrap only; no longer authoritative once Postgres holds a pair |
| `WHOOP_ACCESS_TOKEN` | Optional bootstrap/local-live compatibility value; not needed by the persistent first refresh |

The workflow sets `WHOOP_PIPELINE_USE_LIVE_CLIENT=true` itself. Rotated token values are kept
in Postgres, never written back into GitHub secrets. Keep database backups and credentials
protected. Never upload `data/`, token query results, or unredacted runtime logs as artifacts.

Trigger `Scheduled WHOOP pipeline` manually from Actions once, verify the persisted counts
and checkpoint, and then trust the daily 06:00 UTC schedule. GitHub scheduling is best-effort;
public-repository schedules may be disabled after 60 days of inactivity. Workflow concurrency
prevents overlapping Actions jobs; it does not coordinate independent local runs.

### Recovery and remaining limits

This is one WHOOP account and one writer. Gold and checkpoint commits are atomic in Postgres.
If dbt fails after gold commits, rerun the job (or `whoop-dbt build --target prod` with the
Postgres opt-in); it rebuilds the complete mart from durable gold.

A crash after WHOOP rotates tokens but before the database saves them may require manual
reauthorization and deliberate replacement of the stale stored pair. Changing bootstrap
secrets alone will not override an existing database pair. Do not delete stored tokens as
routine troubleshooting; first confirm the failure and stop competing refreshers.

Raw bronze files and Dagster run history remain ephemeral in scheduled containers. The initial
backfill is 180 days; Phase 3 does not migrate older local DuckDB history automatically.
SQLite/offline SQL tests cannot verify provider permissions, real Postgres behavior, TLS,
transaction-pooler behavior, or token rotation against the live provider. Complete these checks
before Phase 4 assumes the deployed pipeline is persistent.
