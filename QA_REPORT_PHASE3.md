# Phase 3 QA Report — Postgres Persistence + WHOOP Token Lifecycle

Date: 2026-08-30
QA performed by: Claude Code, independent of the two agents (Claude Code partial + Codex
completion) that built this session's changes.

**Headline finding: §1's safety property holds. No unsafe code path exists. No fix was needed.**
Verified by reading the gating code across three independent entry points (Dagster resources,
the dbt CLI wrapper, Alembic migrations) and by actually running the regression test that
proves it — not by trusting the session's own summary.

---

## 1. Safety finding: can any code path reach real Postgres without an explicit, separate opt-in?

**No.** Checked before running pytest or anything else, as instructed.

The gating flag is `WHOOP_PIPELINE_USE_POSTGRES` (mirroring Phase 2's
`WHOOP_PIPELINE_USE_LIVE_CLIENT` exactly), implemented once in
`whoop_pipeline/storage/database.py`:

```python
def is_enabled(flag_name: str) -> bool:
    return os.environ.get(flag_name, "").strip().lower() in ("1", "true", "yes")


def require_postgres_opt_in() -> str:
    if not is_enabled("WHOOP_PIPELINE_USE_POSTGRES"):
        raise RuntimeError("Set WHOOP_PIPELINE_USE_POSTGRES=true to explicitly enable Postgres")
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("WHOOP_PIPELINE_USE_POSTGRES is set but DATABASE_URL is not")
    return database_url
```

This single function is the only path to a real `DATABASE_URL` value, and it checks the
dedicated flag **first**, independently of `DATABASE_URL`'s presence — there is no
`if os.environ.get("DATABASE_URL"): use_postgres = True` pattern anywhere. I verified this is
enforced consistently across all three places a real connection could originate:

1. **Dagster resource wiring** (`orchestration/definitions.py`): `_default_storage_resource()`
   and `_default_whoop_resource()` both check `is_enabled("WHOOP_PIPELINE_USE_POSTGRES")` before
   ever referencing `DATABASE_URL`, and `PostgresBackend(database_url=EnvVar("DATABASE_URL"))`
   is only constructed after that check (plus a redundant second check inside
   `require_postgres_opt_in()`). `EnvVar` additionally defers resolution to Dagster's
   run-launch time and masks the value in logs/UI — stronger than my own in-progress draft,
   which used a plain `os.environ.get(...)` string.
2. **`whoop_pipeline/dbt_cli.py`** (a new `whoop-dbt` CLI entry point wrapping `dbt build`):
   `dbt_environment(target=..., environ=...)` only touches Postgres when `target == "prod"`,
   and that path calls `require_postgres_opt_in()`.
3. **`alembic/env.py`**: even `alembic upgrade --sql` (offline mode, generates SQL text only,
   no connection) still calls `require_postgres_opt_in()` first — there is no "just print the
   SQL" bypass. Tests inject a disposable SQLite connection via
   `config.attributes["connection"]`, and the injection path itself is hard-restricted to
   `sqlite` dialect only (`if supplied_connection.dialect.name != "sqlite": raise RuntimeError`),
   so a test cannot accidentally be pointed at a real Postgres connection through that
   mechanism either.

**The required regression test exists and I ran it** (not just confirmed its presence):
`tests/test_orchestration.py::test_definitions_default_to_local_backend_without_explicit_postgres_opt_in`
sets `DATABASE_URL=postgresql://not-a-real-host/checking-the-gate`, leaves
`WHOOP_PIPELINE_USE_POSTGRES` unset, and asserts `LocalBackend` is still selected.

```
tests/test_orchestration.py::test_definitions_default_to_local_backend_without_explicit_postgres_opt_in PASSED
```

There's a second, matching guarantee at the CLI layer
(`tests/test_parse_database_url.py::test_dbt_cli_requires_explicit_opt_in_and_never_prints_credentials`)
that also passed, confirming `whoop-dbt build --target prod` refuses to run and never touches
the real `DATABASE_URL` value without the flag.

**`scripts/authenticate.py`** (touched this session, +6/-4): unrelated to the Postgres gate —
it's the same manual-only, human-run browser OAuth bootstrap from Phase 1, now returning a full
token pair (with expiry) instead of just an access token. It never references `DATABASE_URL`
and is not called by any automated path. No issue.

**Grep for every `DATABASE_URL` reference** in `src/`, `alembic/`, and `scripts/`: every hit is
either (a) inside `storage/database.py`'s gated parsing/validation functions, (b) a doc comment,
or (c) the two already-covered `definitions.py` lines that are reached only after the flag
check. Nothing bypasses the gate.

**Conclusion: no fix was necessary.** The implementation already matches Phase 2's proven
pattern, and in several respects (deferred `EnvVar` resolution, credential-safe error messages
that never echo the raw URL, TLS enforcement, an allowlist of permitted URL query parameters,
`DBT_ENV_SECRET_WHOOP_PASSWORD`-style secret masking for dbt, restricting the Alembic
connection-injection test seam to SQLite only) it is more defensive than what I had been
building before this session's work was completed by another agent.

---

## 2. Repo and commit integrity

- Working directory used for all verification in this report:
  `C:\Users\levij\Documents\Personal\Code\projectWHOOP`. `git remote -v` confirms
  `origin = https://github.com/levijb/projectWHOOP.git`. This is the real repo.
- **Note on this session's starting environment**: the harness's default working directory for
  this QA session was `C:\Users\levij\Documents\GitHub\projectWHOOP` — the same decoy,
  non-git Phase 1 folder identified and warned about in the original Phase 1 QA report. I did
  not use it; every command in this report was explicitly run against the real checkout above.
  `NEXT_STEPS_FOR_HUMAN.md` (written by the build session) independently flags the same thing:
  *"The similarly named `Documents\GitHub\projectWHOOP` directory is an older Phase 1 copy."*
  This is a pre-existing, known, already-documented condition — not a new problem introduced by
  Phase 3, and I did not act on anything in that decoy folder.
- `git log --oneline --graph --all`: clean, single linear history. Phase 3 added exactly two
  commits, both descending directly from `651c63b` (the last Phase 2 commit):
  ```
  * c524f98 Harden scheduled deployment and document Phase 3 verification
  * b92f780 Add persistent Postgres storage and token lifecycle with offline coverage
  * 651c63b Add scheduled pipeline workflow and Phase 2 docs
  * 729279b Add Dockerfile for one-shot pipeline execution
  * 02cc203 Wire Phase 1 functions into a Dagster asset graph
  * 0ce7d3d Add dbt project for ML feature marts on top of the gold DuckDB tables
  ...
  ```
  No divergent branches, no second history. This is not the Phase 1 decoy-folder problem
  recurring — the ancestry is exactly what it should be.
- `git status --short`: clean. No uncommitted changes left over (including from the earlier,
  interrupted Claude Code build attempt that preceded Codex's completion — nothing was left
  half-applied in the working tree).
- **What "the open workspace was an older Phase 1 copy" meant, concretely**: I confirmed by
  inspecting the diff stats of commit `b92f780` that Phase 2 deliverables were **present and
  modified, not recreated**. `dbt/models/marts/mart_daily_features.sql` shows as a 14-line
  modification (adding the `day_of_week()` macro call and `max()` portability fix on top of the
  existing Phase 2 feature set), not a fresh file. `docs/orchestration.md` (276 changed lines)
  and `Dockerfile` (14 changed lines) are likewise modifications of existing Phase 2 content.
  I additionally read the current `mart_daily_features.sql` directly and confirmed all of
  Phase 2's original feature-engineering columns (rolling averages, `prior_day_strain`,
  sleep-debt trend, rest-day recency) are intact. **Phase 2's work was not missing or stale;
  Phase 3 correctly built on top of it.** The "older Phase 1 copy" language in
  `NEXT_STEPS_FOR_HUMAN.md` refers only to the separate decoy folder noted above, not to
  anything wrong with the checkout Phase 3 actually used.

---

## 3. Full verification suite — real output

Environment: same machine, Python 3.12.1, `pip install -e ".[dev]"` completed with no new
downloads needed (all Phase 3 dependencies were already installed from the interrupted build).

```
$ pytest -v --tb=short
...
======================= 90 passed, 4 warnings in 25.97s =======================
```

Confirmed the count matches the session's own claim exactly (90/90, not just "close to 90").
Full pass list spot-checked against file names — every new file mentioned in the commit stats
(`test_migrations.py`, `test_dbt_postgres.py`, `test_storage_resources.py`,
`test_parse_database_url.py`, expanded `test_token_lifecycle.py` and `test_postgres_backend.py`)
is present and passing, not silently skipped.

```
$ ruff check .
All checks passed!

$ ruff format --check .
55 files already formatted

$ mypy
Success: no issues found in 22 source files
```

The 4 warnings are the same pre-existing, benign ones from Phase 2
(`dagster_dbt.asset_utils` calling a deprecated `AssetExecutionContext.op` internally, and one
`urllib3`/`chardet` version-mismatch notice from `requests`) — not new, not actionable.

**`ci.yml` (push/PR workflow) is untouched**: `git log --oneline -- .github/workflows/ci.yml`
shows no commits since Phase 2's `913503f`, and `git diff 913503f -- .github/workflows/ci.yml`
is empty. Ran its exact command (`pytest --cov=whoop_pipeline --cov-report=term-missing`)
directly: 90 passed, 84% overall coverage, no failures. It would still pass.

---

## 4. Completeness against the Phase 3 brief

| Item | Status | Reason |
|---|---|---|
| Storage backend abstraction (Local vs. Postgres), mirroring `WhoopDataSource` | ✅ | `GoldStorageBackend` Protocol + `LocalBackend`/`PostgresBackend` in `orchestration/resources.py`, same shape as Phase 2's `WhoopDataSource`/`FixtureWhoopResource`/`LiveWhoopResource` |
| Postgres schema via Alembic mirroring gold tables + sync-state table | ✅ | `alembic/versions/0001_initial_schema.py` creates `cycles`, `recovery`, `sleep`, `workouts`, `sync_state`, plus `whoop_tokens`, all under a dedicated `whoop` schema on Postgres (with `REVOKE ALL ... FROM PUBLIC`), verified structurally equal to the SQLAlchemy runtime metadata by `test_migrations.py::test_migrated_schema_matches_runtime_metadata` |
| Idempotent upsert-by-id logic matching Phase 1's DuckDB semantics | ✅ (verified, not just trusted) | `test_postgres_backend.py` has both the direct idempotency tests (run twice, counts stable; update-in-place) I would have written, **plus** two tests I hadn't: an injected-failure test proving the whole gold+checkpoint write is one atomic transaction (`test_failed_transaction_preserves_all_gold_and_checkpoint`), and a test proving persistence survives closing and reopening a fresh `PostgresBackend` instance against the same URL (`test_partial_retry_preserves_history_and_survives_a_new_backend`) |
| dbt `prod` target added, `dev` unchanged and still what `test_dbt_mart.py` uses | ✅ | `dbt/profiles.yml` has both targets; `dbt/models/staging/sources.yml`'s `schema: "{{ target.schema }}"` correctly resolves to DuckDB's `main` for dev and Postgres's `whoop` for prod without hardcoding either; `test_dbt_mart.py` still passes unmodified against `dev` only |
| Token refresh: checks expiry, refreshes, persists durably | ✅ | `oauth.py`'s `ensure_fresh_token`/`WhoopTokenPair` hardened beyond a minimal implementation: rejects an incomplete refresh response outright rather than silently reusing a possibly-invalidated old refresh token, redacts token fields from `repr()`, validates `expires_in` against bool/NaN/negative/overflow edge cases. Persists to Postgres's `whoop_tokens` table only when the same Postgres opt-in is active; local/no-Postgres runs never persist, matching the brief |
| `scheduled-pipeline.yml` references the new flag/secret correctly | ✅ | Sets `DATABASE_URL` and `WHOOP_PIPELINE_USE_POSTGRES` from secrets, `WHOOP_PIPELINE_USE_LIVE_CLIENT=true` directly (not a secret — correct, it's not sensitive), has pre-flight shell checks that fail with a clear message if secrets are missing, and a `concurrency` group specifically to prevent two runs racing a token rotation. Still cannot succeed without secrets configured — expected |
| Tests actually cover the above meaningfully | ✅ | Spot-checked three: the atomicity test above uses a real SQLAlchemy `event.listen` hook to inject a mid-transaction failure (not a mock); `test_dbt_postgres.py` offline-compiles the actual `prod` dbt target and additionally cross-executes the Postgres-flavored `daily_summary` view SQL against DuckDB loaded with the same fixtures, asserting **row-for-row equality** with the native DuckDB view — a real semantic check, not just "it parses"; `test_token_lifecycle.py` has 9 parametrized cases for malformed `expires_in` values alone (`None, 0, -1, True, nan, inf, 1e+100`, etc.) |

---

## 5. What I fixed vs. what needs the human

**Fixed:** nothing — §1 found no unsafe pattern, so no code change was made this session. This
report is purely verification.

**Needs the human**, per the build session's own `NEXT_STEPS_FOR_HUMAN.md` (I read it and it is
detailed and accurate; summarizing rather than duplicating it in full here):

1. Run `alembic upgrade head` once against the real Supabase database (with
   `WHOOP_PIPELINE_USE_POSTGRES=true`, `WHOOP_PIPELINE_USE_LIVE_CLIENT=false`) to create the
   `whoop` schema and tables. Nothing in this codebase does this automatically.
2. Run the one-time real-pull smoke test exactly as documented (enable the live client only
   *after* migrating, to avoid inserting fixture-derived synthetic IDs).
3. Verify the Docker image actually builds — Docker still isn't installed on the machine this
   was built on; every command the image runs was verified outside a container, but the image
   itself has never been through a real `docker build`.
4. Add the six GitHub Actions secrets listed in `NEXT_STEPS_FOR_HUMAN.md` only after the smoke
   test passes.
5. Decide whether to delete the decoy `Documents\GitHub\projectWHOOP` folder (documented,
   pre-existing, not touched this session).

---

## 6. Restated instruction to the human: check Supabase directly

**Do not take my word or the build session's word for this — open your Supabase project
dashboard → Table Editor yourself and confirm there is nothing there yet.** If this session's
safety gate worked as verified above, your database should be completely empty (no `whoop`
schema, no tables, no rows) — the migration hasn't been run yet either. If you find anything
unexpected in your Supabase project, stop and don't proceed with the smoke test — that would
mean something reached your database despite the code-level checks in this report, and needs
to be re-investigated before you trust any of the above.

---

## 7. Ready for Phase 4?

**Not yet — but for a reason entirely outside this session's or my control: the human's manual
Postgres verification (§5 above / `NEXT_STEPS_FOR_HUMAN.md` §§1–4) hasn't happened yet.**

Everything checkable without a live database connection checks out: the safety gate is real and
tested (not just claimed), the repo/commit history is clean and correctly descended from Phase
2, all 90 tests pass, lint/format/type-checking are clean, the existing push/PR CI is untouched,
and the Phase 3 brief's items are all genuinely implemented and tested — several more
thoroughly than the original brief required (transaction-atomicity testing, semantic
cross-dialect SQL validation, credential-redaction testing). This is solid, careful engineering
work, not a self-report that turned out to be inflated.

**Recommendation:** once the human completes the real-Postgres smoke test in
`NEXT_STEPS_FOR_HUMAN.md` and confirms the Supabase Table Editor check above, Phase 3 is done
and Phase 4 (the ML modeling suite, consuming `mart_daily_features`) can begin on a genuinely
persistent pipeline. Until then, treat the Postgres path as implemented-and-tested-offline but
**operationally unverified** — the same status Docker has carried since Phase 2.
