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
WHOOP v2 (manual credentials, runtime only)
  -> rate-limit-aware client + incremental sync
  -> data/bronze/<record-type>/<pull-date>.jsonl
  -> typed pandas silver transforms + Pandera contracts
  -> data/processed/whoop.db
       cycles | recovery | sleep | workouts | daily_summary view
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

Phase 1 does not include machine learning, dbt, orchestration, Postgres, dashboards, scheduled
live pulls, LangChain, or Databricks. Those belong to later phases (including the recovery
prediction work described above) after the local ingestion contract is proven stable.
