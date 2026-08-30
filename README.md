# **ProjectWHOOP**
**"Personalized Recovery and Performance Optimization System: Multi-Dimensional Analysis of Physiological Readiness Using Whoop Wearable Data"**

---

## **Project Overview**

**Objective:**
Build an intelligent system that:
1. Collects comprehensive Whoop data via API
2. Stores in structured SQL database
3. Performs time-series analysis
4. Predicts future readiness states
5. Provides personalized recommendations
6. Visualizes long-term trends

---

## **Core Research Questions**

1. **Predictive Modeling: Recovery Score Prediction with Continuous Learning**
   - **Objective:** Build an adaptive, self-improving recovery prediction system that mimics Whoop's proprietary algorithm through iterative learning from personal physiological data. Design a system to implement continuous learning where the model evolves daily, learning from prediction errors and adapting to your changing physiology, behaviors, and fitness level over time.

2. **Pattern Recognition:**
   - What behaviors most impact recovery? (quantified)
   - Identify weekly/monthly cycles in performance
   - Detect early warning signs of overtraining

3. **Optimization:**
   - What's the ideal sleep duration for maximum recovery?
   - How does strain distribution (steady vs. spiky) affect recovery?
   - Optimal timing for high-strain activities?

4. **Correlational Analysis:**
   - Relationship between HRV trends and illness
   - Impact of sleep consistency on recovery
   - Strain-recovery balance over time

5. **Visualization:**
   - Make attractive, informative visualizations of health trends
   - Create dashboard to be updated daily

The detailed recovery-prediction modeling spec lives in
[Recovery_Prediciton/README.md](Recovery_Prediciton/README.md) and is later-phase work, built on
top of the Phase 1 ingestion foundation described below.

---

## Phase 1: ingestion foundation

Phase 1 focuses only on reliable collection, medallion-style local storage, validation, and a
unified daily view; modeling and hosted services are intentionally out of scope for now.

### Current architecture

```text
WHOOP v2 (explicit live opt-in; rotating tokens persisted for scheduled runs)
  -> rate-limit-aware client + incremental sync
  -> data/bronze/<record-type>/<pull-date>.jsonl
  -> typed pandas silver transforms + Pandera contracts
  -> local DuckDB (default) OR private Postgres whoop schema (explicit opt-in)
       cycles | recovery | sleep | workouts | daily_summary view
       -> dbt marts (mart_daily_features: rolling averages, lags, rest-day recency)
```

- `src/whoop_pipeline/client.py` provides injected HTTP transport, bounded retry behavior, and
  pagination.
- `src/whoop_pipeline/ingestion.py` overlaps the last successful date so pending scores can be
  refreshed, and advances sync state only after a complete pull.
- `src/whoop_pipeline/transform/flatten.py` turns verified v2 records into typed DataFrames.
- `src/whoop_pipeline/storage/` owns atomic JSONL/state writes and idempotent DuckDB upserts.
- `src/whoop_pipeline/validation/schemas.py` rejects invalid silver data before a database is
  opened.
- `tests/fixtures/` contains realistic scored and unscored responses; the test suite never uses
  credentials or live HTTP.

The detailed field contract and aggregation choices are in [docs/data_model.md](docs/data_model.md).

`src/whoop_client.py` and `src/whoop_oauth.py` are the original, pre-Phase-1 client and OAuth
helpers. They're kept for now because `notebooks/WHOOP_EDA.ipynb` still imports them directly;
see [NEXT_STEPS_FOR_HUMAN.md](NEXT_STEPS_FOR_HUMAN.md) for the plan to retire them once that
notebook is migrated to `whoop_pipeline`.

## Phase 2: feature marts and orchestration

Phase 2 adds a feature-engineering layer on top of the Phase 1 gold tables, and makes the
pipeline runnable end to end instead of only by hand.

- `dbt/` is a dbt project that declares `cycles`, `recovery`, `sleep`, `workouts`, and
  `daily_summary` as **sources** (Phase 1's Python code owns that logic; dbt only builds on
  top of it) and defines `mart_daily_features`, the feature set the Phase 4 recovery-prediction
  model will consume.
- `src/whoop_pipeline/orchestration/` wires the Phase 1 functions into Dagster software-defined
  assets: `raw_whoop_data -> bronze_partitions -> silver_frames -> gold_tables ->
  mart_daily_features` (the last via `dagster-dbt`, so dbt's lineage is a first-class part of
  the graph, not hidden behind a subprocess call). The WHOOP data source is a swappable
  resource; it defaults to Phase 1's test fixtures and only uses the real client when
  `WHOOP_PIPELINE_USE_LIVE_CLIENT` is explicitly set (see
  [docs/orchestration.md](docs/orchestration.md) for why that's a separate flag from credential
  presence).
- `Dockerfile` packages the pipeline as a one-shot job (`dagster job execute`), not a
  persistent server.
- `.github/workflows/scheduled-pipeline.yml` runs that image on a daily cron, separate from the
  existing push/PR `ci.yml`.

Full details, including how to run the asset graph locally with zero credentials, are in
[docs/orchestration.md](docs/orchestration.md).

## Phase 3: production hardening

The scheduled pipeline can now retain history, checkpoints, feature marts, and rotating WHOOP
tokens in Postgres. Local fixture development still defaults to DuckDB with no credentials.
Production requires both explicit opt-ins: `WHOOP_PIPELINE_USE_LIVE_CLIENT=true` and
`WHOOP_PIPELINE_USE_POSTGRES=true`, plus `DATABASE_URL` and WHOOP bootstrap credentials.

Alembic owns the private `whoop` schema. Gold writes and the checkpoint commit together, so
failed processing cannot skip a sync window. Stored token pairs take precedence over bootstrap
secrets and are refreshed before expiry. The dbt production target uses the same database.
The workflow serializes runs to protect token rotation; bronze files remain ephemeral in CI.

See [SESSION_3_SUMMARY.md](SESSION_3_SUMMARY.md) for the verified offline results and
[NEXT_STEPS_FOR_HUMAN.md](NEXT_STEPS_FOR_HUMAN.md) for the required real-Postgres smoke test,
Docker build, and secret setup. **No live WHOOP or Postgres connection was made during development.**

### Install and verify

Python 3.11 or newer is required.

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
pytest --cov=whoop_pipeline --cov-report=term-missing
```

All tests mock HTTP and work with zero environment variables configured. CI runs the same lint,
type, and test commands on pushes and pull requests without repository secrets.

### Manual data collection

Real collection is deliberately not automatic. Complete the credential steps in
[NEXT_STEPS_FOR_HUMAN.md](NEXT_STEPS_FOR_HUMAN.md), then run:

```bash
python scripts/download_whoop_data.py --days-back 180
```

Subsequent runs use `data/_state/sync_state.json` and overlap the last successful UTC date.
Local `.env`, bronze files, sync state, and DuckDB databases are ignored by Git.

### Scope

As of Phase 3, persistent Postgres gold/checkpoint/token storage and the production dbt target
are implemented alongside the safe local defaults. Real Postgres and Docker verification remain
operator steps. Phase 4 is the ML modeling suite; dashboards, LangChain, and Databricks remain
out of scope.
