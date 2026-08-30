# Phase 3 fix summary — Postgres daily-summary ambiguity

## Diagnosis

**Neither of the two proposed `workouts.cycle_id` situations applied.**

The migration, SQLAlchemy `WORKOUTS_TABLE`, `flatten_workouts`, and workout validation schema
do not define or populate a workout `cycle_id`. Both the original DuckDB view and the
Postgres migration used `w.*`; neither used `EXCLUDE`. Python's separate `join_daily`
calculation computes daily aggregates, not a cycle ID stored back into the workouts table.
No table column needed to be removed or repurposed.

The real failure was the final join:

```sql
LEFT JOIN ranked_main_sleep AS s
    ON c.cycle_id = s.cycle_id AND s.row_number = 1
LEFT JOIN workout_by_cycle AS w USING (cycle_id)
```

The sleep `ON` join preserves both `c.cycle_id` and `s.cycle_id` in its output. The next
`USING` therefore sees two left-side columns with the same name. Qualifying `c.cycle_id` in
the final SELECT does not resolve ambiguity in the FROM clause. This matches
[PostgreSQL's documented join behavior](https://www.postgresql.org/docs/current/queries-table-expressions.html#QUERIES-JOIN).

DuckDB accepted this join structure, so the earlier cross-dialect test could not establish
PostgreSQL compatibility. The first real smoke test exposed that limitation.

## Fix

- Changed the final join in both the migration and DuckDB view to
  `LEFT JOIN workout_by_cycle AS w ON c.cycle_id = w.cycle_id`.
- Replaced `w.*` in both workout-assignment CTEs with the five needed workout fields:
  `workout_id`, `strain`, `kilojoule`, `start_at`, and `end_at`. This is defensive
  projection; the decisive ambiguity fix is the qualified final join.
- Kept temporal assignment unchanged: a workout belongs to a cycle when its start is inside
  the half-open cycle interval. Open-ended cycles remain eligible; if intervals overlap,
  the latest-starting cycle wins. The `cycle_rank = 1` condition still prevents double counting.
- Preserved the view's column names/order, main-sleep policy, null behavior, aggregate
  calculations, and one-row-per-cycle shape.
- Corrected `0001` in place because the supplied context confirms it never successfully
  applied. No replacement migration, database cleanup, or state manipulation was performed.
- Reviewed dbt source/model/macro SQL. No analogous wildcard/EXCLUDE/ambiguous-USING issue
  was present, so no dbt model change was necessary.
- Made the existing cross-dialect test split SQL with `sqlparse`; splitting on a bare
  semicolon incorrectly truncated a statement when the explanatory comment contained one.
- Added the Postgres SQL review checklist and reproducible embedded-engine checks to
  `docs/orchestration.md`, and updated the human retry instructions.

## Verification

No native PostgreSQL executables were found on Windows PATH, standard PostgreSQL install
paths, or Ubuntu WSL. Instead, installed **PGlite 0.5.8** into a temporary directory.
[PGlite](https://pglite.dev/docs/) runs an embedded PostgreSQL WASM engine without a server;
this runtime reported **PostgreSQL 18.3**. It is a test-only optional dependency, not added to
the application's Python dependencies, Docker image, or normal CI requirements.

The regression test first ran against the **original, unchanged migration** and reproduced:

```text
SQLSTATE 42702: common column name "cycle_id" appears more than once in left table
```

After the fix, `tests/test_postgres_runtime.py` successfully runs the complete emitted
Alembic migration in memory, inserts fixture-derived rows, and compares every output column
and row value against fixture DuckDB. It additionally asserts expected workout counts for:

1. Baseline fixtures, including a cycle with no main sleep.
2. Overlapping/open-ended cycles and a cycle with no workouts.
3. A workout exactly at the next cycle's start, one at a closed cycle's end in a gap,
   and one before every cycle.

The Node subprocess receives no WHOOP/database credentials; network sockets and fetch are
blocked. Alembic only renders SQL with an explicit synthetic URL. Tests use temporary files
and in-memory databases, not local real wearable data. Installing the package required
Node's system-CA option on this Windows machine; TLS verification was never disabled.

Final results:

| Check | Result |
|---|---|
| Full pytest suite with optional embedded runtime enabled | 93 passed |
| Ruff lint | Passed |
| Ruff formatting check | Passed |
| mypy | Passed; 22 source files |
| Existing fixture Dagster/dbt integration | Passed within full suite |
| Embedded PostgreSQL migration/view | Original fails as reported; fixed succeeds in all three cases |

The full suite retains its existing requests/chardet and dagster-dbt deprecation warnings.
Without `PROJECTWHOOP_PGLITE_DIR` configured, normal Python-only CI skips the three optional
embedded-engine tests; the existing 90 tests still run. The original live-resource safety
tests and Postgres opt-in were not weakened. Ruff also added one formatting-only blank line
inside the independent QA report's Python code block; its findings were not changed.

## Limits and safety

- No connection to Supabase or any remote Postgres database was made.
- No live WHOOP request, OAuth flow, Docker operation, signup, workflow dispatch, or push.
- Did not read or modify the real `.env`, alter database state, or remove the older workspace.
- Embedded PostgreSQL verifies the SQL engine behavior, not Supabase permissions, TLS,
  pooler behavior, or the deployed schema. The human still needs to retry the live migration.
- Alembic's deliberate lack of implicit dotenv loading remains. Use the existing explicit
  dotenv runner when the URL is only in `.env`; do not relax the opt-in safeguards.
- The pasted terminal command contained a database password. Initial attachment redaction
  failed to fully mask that line in tool output; sanitized temporary copies were corrected.
  The value was not used to connect or written into project files. Rotate it and update
  the local URL and any Actions secret before the live retry.

## Your next step

After rotating the exposed password and making the updated URL available in the shell,
with `WHOOP_PIPELINE_USE_POSTGRES=true`, simply rerun:

```powershell
alembic upgrade head
```

If the updated URL is stored only in `.env`, remove any stale shell `DATABASE_URL` first
and use:

```powershell
$env:WHOOP_PIPELINE_USE_POSTGRES = "true"
python -m dotenv -f .env run --no-override alembic upgrade head
```

No `stamp`, `downgrade`, schema deletion, or fixture seeding is needed for the reported
failed initial migration. Keep the live WHOOP flag off until you deliberately resume that
part of the smoke test. Do not materialize fixture assets into production history; use a
separate disposable database if you want a fixture-only hosted test.

See [NEXT_STEPS_FOR_HUMAN.md](NEXT_STEPS_FOR_HUMAN.md) for the remaining supervised activation
steps. Phase 4 should still wait for successful real-Postgres verification.
