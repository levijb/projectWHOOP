# Phase 3 session summary — production hardening

## Handoff and location

The active checkout is `C:\Users\levij\Documents\Personal\Code\projectWHOOP`.
The initially opened `C:\Users\levij\Documents\GitHub\projectWHOOP` is an older Phase 1
copy without Git history, dbt/Dagster, or `.env`. It was not modified.

Reviewed the pasted planning prompts and this checkout's Phase 1/2 reports and code. Phase 2
was committed through `651c63b`; Claude had already drafted uncommitted Phase 3 changes.
Those drafts were reviewed, completed, and corrected rather than discarded. The local
`DATABASE_URL` key exists; its value was not displayed, validated, or used.

## Completed

- Swappable `GoldStorageBackend`, default `LocalBackend`, and SQLAlchemy-backed
  `PostgresBackend`, injected into Dagster ingestion and gold storage.
- Alembic revision `0001` and revision template for the private `whoop` schema: cycles,
  recovery, sleep, workouts, daily-summary view, sync state, and current WHOOP token pair.
  Stable cycle/user IDs use 64-bit integers; timestamps preserve UTC/timezone semantics.
- Validated ID-based replacement matching the existing DuckDB semantics. Postgres commits
  all gold tables **and the checkpoint atomically**. Local storage advances JSON state only
  after DuckDB commits, so failures cause safe replay.
- Explicit Postgres opt-in independent of credential presence, alongside the original live
  WHOOP opt-in. Both safety regression tests remain. Credentials use Dagster `EnvVar`
  references rather than literal resource configuration.
- Production dbt profile, matching source schema, actual production target selection, and
  portable mart SQL. Local resource paths feed the exact same DuckDB file to dbt.
- Shared, credential-safe URL parsing. Passwords are decoded once; TLS is required.
  `whoop-dbt` passes production settings through the child environment, with dbt secret
  redaction, rather than printing password-bearing shell exports.
- Persistent token lifecycle for live/Postgres only: prefer stored tokens, refresh unknown or
  near-expiry bootstrap/stored tokens, validate the rotated pair and expiry, save before
  collection, and preserve the prior pair on failure. The first persistent run needs a
  refresh token but not a bootstrap access token. Local live-only behavior is unchanged.
- The human-only OAuth bootstrap helper now retains both tokens and their expiry. It was
  **not run**. The legacy `src/whoop_client.py`/`src/whoop_oauth.py` and notebooks are untouched.
- Scheduled workflow passes Postgres configuration and both opt-ins, refuses an unconfigured
  persistent backend, and serializes runs without cancelling an in-flight token rotation.
  Existing push/PR `ci.yml` is unchanged.
- Docker build includes required README/license inputs and migrations; `.dockerignore`
  excludes credentials, local wearable data, and generated files.
- Updated README, orchestration documentation, `.env.example`, and detailed human activation
  and smoke-test instructions.

## Corrections to the interrupted draft

The draft advanced sync state at the bronze step, before validation/gold persistence.
That could skip data after a failure; checkpointing now follows successful gold persistence.

The draft's Postgres dbt profile was not selected by Dagster, sources still assumed `main`,
and the mart still called DuckDB-only SQL despite new portability comments. All three paths
are now connected and tested. Its URL-export helper also failed to decode passwords and
would have printed them; it is now a safe command wrapper.

The live resource required bootstrap tokens even when a newer pair existed in the database,
and it would refresh local-only tokens without persisting the rotated pair. Stored credentials
can now stand alone, and automatic rotation is restricted to the persistent path.

Tests initially created SQLAlchemy metadata directly, bypassing the migration. They now apply
the actual Alembic revision, compare the resulting schema, and exercise upgrade/downgrade.
The initial migration is still pre-deployment; no existing live schema was modified.

## Exact testing approach and results

**All tests use synthetic fixtures, mocks, and temporary local files.**

1. **Disposable SQLite through SQLAlchemy:** `tests/conftest.py` creates a temporary file,
   explicitly injects its connection into Alembic, and runs `upgrade head`. SQLAlchemy's
   schema translation maps `whoop` to SQLite's default schema. Backend SQL uses the same
   transaction/parameterized delete/insert operations as production.
2. Tests cover idempotency, changed IDs, partial retries retaining history, empty/nullable
   data, new backend instances, sync and token persistence, validation failures, and injected
   failures during gold or checkpoint writes. The prior data/checkpoint survive rollbacks.
3. **Mocked WHOOP refresh endpoint:** fresh/expired/near-expiry cases, bootstrap-only refresh,
   rotation across database reopen, invalid/failed responses, stored-token preference without
   bootstrap secrets, and unchanged local-only behavior.
4. **Production SQL without Postgres:** Alembic emits Postgres DDL in SQL-only mode.
   The real dbt `prod` model compiles with introspection/cache population disabled and native
   Postgres connections blocked. Fixture DuckDB executes the generated Postgres daily view
   and compiled mart as an additional semantic cross-check.
5. **Dagster integration:** full fixture graph through real dbt, plus local/SQLite-backed
   Python asset graphs with successful and failed validation. Resource flag combinations
   verify storage, WHOOP source, dbt target, and credential-safe configuration.
6. The suite strips inherited credential variables before collection, blocks external Python
   sockets/native psycopg2 calls in-process, disallows dotenv loading by pipeline code during
   tests, and disables dbt telemetry. Loopback remains available for Windows asyncio.

Final local checks on Windows / Python 3.12.1:

| Check | Result |
|---|---|
| `python -m ruff check .` | Passed |
| `python -m ruff format --check .` | Passed |
| `python -m mypy` | Passed; 22 source files |
| `python -m pytest --cov=whoop_pipeline --cov-report=term-missing -q` | **90 passed**, 84% total coverage |
| Non-editable wheel build/install | Passed |
| Scratch `dbt parse --target dev` | Passed |
| Installed-package multiprocess `dagster job execute` | Passed |
| Scratch `whoop-dbt docs generate --target dev` | Passed |
| Packaged fixture mart | Expected 2 rows |
| Existing `ci.yml` | No changes |

The packaging check used a temporary wheel build from copied package inputs, installed
non-editably into a temporary virtualenv reusing the machine's installed dependencies. Import
verification confirmed execution from that virtualenv's `site-packages`, not the editable
checkout. Runtime dbt/fixtures/migrations were copied to a separate scratch directory with no
`.env`. A startup guard blocked external sockets and psycopg2 in the spawned processes.
This checks packaged execution, not a fresh Linux dependency installation.

The scratch guard caught dbt's optional PyPI version lookup; blocking it with a normal socket
error allowed dbt's offline fallback. No WHOOP request or Postgres connection was made.
Initial wheel building without build isolation lacked Hatchling; normal isolated wheel
building fetched the build dependency and succeeded without disabling TLS.

Non-failing warnings remain from the existing machine's requests/chardet combination and
upstream dagster-dbt deprecated context access. These were not hidden or fixed by mutating
unrelated global dependencies.

## Judgment calls and limitations

- **Private schema:** all wearable and token tables live in `whoop`, not Supabase's commonly
  exposed `public` schema. The operator must keep it out of the Data API and protect database
  access/backups. Token text is not application-encrypted.
- **Single account and writer:** ID replacement mirrors Phase 1. The workflow prevents its
  own overlapping jobs; independent local processes are not coordinated by a distributed
  lock. Do not run competing refreshers.
- **Refresh failure window:** WHOOP rotation and database persistence cannot be one atomic
  transaction. A crash between them may require manual reauthorization. No blind retry or
  fallback to invalidated bootstrap tokens is attempted.
- **Finite pull duration:** freshness is checked before each collection batch, not between
  individual pagination calls. An unusually long pull or revoked token may fail and need a
  later run; checkpoints preserve safe replay.
- **Durability scope:** gold, checkpoint, tokens, and the mart persist. Bronze partitions and
  Dagster history remain ephemeral in scheduled containers. Existing older local DuckDB
  history is not automatically migrated; initial backfill remains 180 days.
- **SQLite is not Postgres:** the substitute does not establish provider/TLS/pooler behavior,
  permissions, Postgres runtime type behavior, or concurrent-write behavior. Executing
  Postgres SQL on DuckDB is a cross-check, not proof of full dialect compatibility.
- **Real smoke test required:** no connection to the live database or WHOOP was made.
  No real `.env` values were consumed, no OAuth browser flow was run, and no signups,
  GitHub secret changes, workflow dispatches, or pushes were performed.
- **Docker remains unverified:** Docker was neither installed nor used. Linux/Python 3.11
  container building is an explicit human step.
- **Hosted configuration unverified:** local success does not mean the remote schedule is
  enabled or its secrets exist. See `NEXT_STEPS_FOR_HUMAN.md`.

## Handoff

Follow [NEXT_STEPS_FOR_HUMAN.md](NEXT_STEPS_FOR_HUMAN.md): review/push the commits, apply
migrations, run the real-Postgres/live-WHOOP smoke test yourself, verify Docker, then configure
the repository secrets and trigger one supervised workflow run.

**Phase 4 is the ML modeling suite consuming `mart_daily_features`.** Once the human verifies
and enables the production path, Phase 4 can rely on a genuinely persistent pipeline.
The modeling phase must still choose prediction-time features and evaluate temporal leakage;
Phase 3 does not claim every current mart column is safe as a same-day predictor.
