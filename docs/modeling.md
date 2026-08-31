# Modeling capability and deliberate activation

**Models are validated on synthetic data, not trained for real personal forecasts.**
Synthetic benchmarks test the software, not the reliability of personal forecasts or health
flags. Real training requires sufficient history and deliberate activation through
[SETUP.md](../SETUP.md#model-readiness).

## Local synthetic experiment and MLflow

From the checkout, preferably in a virtual environment:

```powershell
python -m pip install -e ".[modeling]"
whoop-model-demo --days 180 --seed 42 --output .modeling/synthetic
$env:MLFLOW_ENABLE_TELEMETRY = "false"
mlflow ui --backend-store-uri sqlite:///.modeling/synthetic/mlflow.db --host 127.0.0.1 --port 5000
```

Open `http://127.0.0.1:5000`. The equivalent demo invocation is
`python -m whoop_pipeline.modeling.demo`. It has **no real-data mode**, reads no gold database,
and does not load `.env`. `.[dev]` includes modeling dependencies, so the existing CI install
command covers this suite. Ingestion-only installations do not need MLflow/scikit-learn;
disabled model assets import neither.

The generator uses seeded NumPy draws: AR(1) stress (0.78 coefficient), random stress shocks,
weekly strain variation, lagged recovery/debt, independent noise, and bounded plausible
values. Labels are not handpicked/rebalanced. Synthetic daily rows execute the **actual mart
SQL and macro** in memory, additionally cross-checked with `dbt build --select
mart_daily_features`. Tests cover 60, 120, 180, and 240-day histories.

Git-ignored `.modeling/synthetic/` contains `mlflow.db` (SQLite tracking and registry),
`mlruns/` (artifacts), and `synthetic_results.json`. No custom registry table or hosted service
is used. SQLite supports MLflow's model registry. Explicit local clients ignore ambient
remote tracking/registry URIs, disable telemetry, and reject remote/out-of-root artifacts.
Synthetic experiments/models are separated from real ones and the demo sets no active alias.

Artifacts use cloudpickle to retain custom preprocessing/incremental state. **Only load
artifacts produced by this trusted local checkout/store**: unpickling can execute code.
Keep artifacts private and use the matching project/Python environment. Dependencies are
downloaded at installation; model execution is offline. MLflow 3.15.2 requires pandas <3;
the verified combination uses pandas 2.3.3.

## Exact feature and target contract

The mart already contains `recovery_score`; a target join is unnecessary. These **12 exact
mart columns** are used by all supervised variants, in this order:

```text
day_of_week
cycle_strain
recovery_score
hrv_rmssd_milli
sleep_debt_need_hours
recovery_score_7d_avg
cycle_strain_7d_avg
hrv_rmssd_milli_7d_avg
prior_day_strain
sleep_debt_7d_avg_hours
sleep_debt_trend_hours
days_since_last_low_strain_day
```

IDs/timestamps are excluded from model inputs. `day_of_week` is a numeric baseline feature
(UTC Sunday=0). Rolling means include the current cycle and count cycles, not calendar days;
rest-day recency counts cycles since strain <8. The DuckDB day-of-week macro now explicitly
extracts UTC, matching Postgres independently of the machine timezone.

Both supervised tasks predict **the immediate next observed cycle**, approximately tomorrow.
The target for feature row N is recovery at N+1. Low recovery means **score <=33**, aligned
with WHOOP's published red zone; it is a modeling judgment, not a clinical cutoff or a
validated train/rest recommendation. Current recovery and current rolling values are valid
for next-cycle forecasting; they would leak the target in a same-cycle model.

Rows must belong to one user, with unique IDs/times, and are sorted chronologically. Shift
targets **before** removing missing outcomes: pending N+1 must never become a label from N+2.
The last row has no known future label. Median imputation/scaling fit on training data only;
there is no filling from future observations. Entirely missing training columns use the
imputer's fixed zero fallback. Pending targets never become classifier negatives.

Historical gold/dbt records are mutable, not point-in-time snapshots. Open-cycle strain may
be partial when forecasting, then final in a backtest; later corrections can revise scores.
Before interpreting real backtests, establish a consistent forecast time and collect
prospective predictions. Temporal label safeguards cannot reconstruct missing historical
feature snapshots. The first serving table stores forecasts, not a feature snapshot archive.

## Validation, models, and uncertainty

Expanding validation starts with **60 labeled training pairs**, purges one pair at the
boundary, and tests the next 14. Each subsequent fold expands training by 14. A timestamp
assertion prevents training labels reaching test origins. At least two full folds are needed;
short trailing remainders are not scored. Anomaly models use the same splitter without a
future-target gap. Every model is checked this way, never with random k-fold or one holdout.

Metrics are fold means/population standard deviations and contributing-fold counts. Regression
logs MAE (score points), MAPE (percent of actual), and R². Zero actuals are excluded from MAPE
and counted; small nonzero scores can dominate MAPE. Logistic regression logs precision/recall
at probability 0.5 and AUC. Single-class training folds are skipped; single-class test folds
omit undefined AUC. Undefined precision/recall use zero with positive counts logged.

| Model | Choice |
|---|---|
| Ridge | alpha=10, batch SVD fit; reference |
| SGDRegressor | squared error, L2 alpha=0.01; intended incremental serving candidate |
| GradientBoostingRegressor | 80 depth-2 trees; available challenger, never promoted by synthetic results |
| LogisticRegression | balanced class weights, C=1; requires both classes |
| Robust anomalies | previous 28-cycle median/MAD, k=3.5, minimum 14 observed values per field |
| IsolationForest | 100 trees, contamination=0.03; unvalidated upgrade path |

Anomalies use `hrv_rmssd_milli`, `resting_heart_rate`, `skin_temp_celsius`, and
`respiratory_rate`. The reader joins gold recovery and latest main (non-nap) sleep for fields
not in the mart. Temperature deviation is calculated against the personal baseline, not
assumed to be an API deviation column. SpO2 is available but excluded from this initial set.
Robust scale floors are 1 ms, 1 bpm, 0.05 C, and 0.1 breaths/min. A row never enters its own
baseline; missing baseline scores stay NaN. Absence of a flag is not evidence of health.
Synthetic extreme outliers must be flagged and a central normal row must not be flagged.
Walk-forward anomaly flag rates are **not diagnostic accuracy**; contamination is an assumption.

`retrain_decision(errors, days_since_update=...)` is pure. Errors are predicted minus actual,
in **score percentage points**, consistent with the original spec's MAE units. The ordered
error windows count observed forecast cycles (normally daily); gaps do not fabricate zeros.

| Priority | Condition | Result |
|---|---|---|
| 1 | Latest absolute error >15 points | `emergency`, full refit |
| 2 | Absolute last-7 mean error >5 points | `bias_correction`, incremental update |
| 3 | Last-7 MAE exceeds preceding-7 MAE by >1 point | `drift_correction`, incremental update |
| 4 | >=7 elapsed calendar days since update | `routine`, incremental update |
| 5 | Otherwise | `stable`, reuse model |

The >1-point drift margin makes the spec's unquantified upward trend reproducible without
reacting to floating-point noise. Emergency/bias thresholds remain strictly greater-than.
SGD `partial_fit` only consumes new labeled pairs; its imputer/scaler stay frozen so coefficient
coordinates remain stable. Each new version logs a **fresh-fit walk-forward reference**,
explicitly tagged; that is not retrospective performance of the exact online snapshot.
Stored prospective forecast errors will provide the latter evidence. Partial updates are
not guaranteed to improve performance and there is no automatic challenger promotion.

Intervals use a separate seeded **moving-block residual bootstrap**, 1,000 resamples of
7-residual blocks, 90% percentile bounds, clipped to [0,100]. At least 20 out-of-sample
actual-minus-predicted residuals are needed (112 in the default experiment). These are
approximate individual prediction intervals, not a CI on the mean, guaranteed coverage,
or clinical confidence. Real coverage needs later calibration.

## Serving and guarded daily updates

New Alembic revision **0002**, following the already-applied 0001, adds private
`whoop.predictions`. `cycle_id` and `model_name` form its key; cycle_id is the **feature/origin
cycle**. `target_cycle_id` starts null and is resolved when the next cycle arrives; future IDs
are never guessed. Other columns are `model_version` (MLflow version), `origin_at`,
`created_at`, `predicted_value`, `ci_lower`, `ci_upper`, `actual_value`, and signed `error`.
Score/interval range constraints are enforced. `ON CONFLICT DO NOTHING` keeps the original
forecast/version on retries. Outcome corrections can change actual/error only. Settlement
does not skip pending targets or attach outcomes whose cycles started before issue time.

The downstream `daily_model_update` asset reads the mart/history, settles outcomes, computes
errors, decides whether to update SGD, logs/version-controls its snapshot, and persists the
next prediction. It serves recovery regression; the classifier/anomaly variants are available
through the suite and MLflow, not additional serving tables in this phase.

**The existing ingestion job and scheduled workflow are unchanged.** The separate
`whoop_model_update_job` selects only this downstream asset and assumes ingestion/dbt already
succeeded. Resource switches `enabled` and `allow_real_training` both default false. The
disabled branch creates no tracking files and reads no database. Real Postgres also requires
the existing `WHOOP_PIPELINE_USE_POSTGRES=true` and matching `DATABASE_URL`.

Only after the real-data readiness review, create a reviewed Dagster config:

```yaml
resources:
  modeling:
    config:
      enabled: true
      allow_real_training: true
      tracking_dir: .modeling/real
```

Manually apply `alembic upgrade head` with the existing Postgres opt-in, run ingestion/dbt,
then deliberately run:

```powershell
dagster job execute -m whoop_pipeline.orchestration.definitions -j whoop_model_update_job -c your-reviewed-config.yaml
```

**These commands access real data. Keep modeling out of Actions until readiness is established.**
Use one persistent local MLflow directory and one writer. A local file lock rejects overlapping
Dagster updates sharing that directory; it cannot coordinate separate hosts/directories.
Activation precedes SQL prediction insertion so a failed insert retries without another
partial_fit. Crashes before activation may leave unused registry versions. MLflow and SQL
are not a distributed transaction; preserve both stores together.

For deliberate local DuckDB modeling, first migrate the separate
`.modeling/real/predictions.db` through Alembic's existing injected SQLite-connection mechanism
(shown in `tests/conftest.py`). Runtime code never creates serving tables. Prefer the
synthetic demo for routine experimentation; it needs no serving database setup.

## Readiness and Phase 5

The guard requires **90 labeled next-cycle pairs over at least 90 days**, one user, recent
features (<=2 days old), and explicit manual activation. Aim for **120-180+ days**, enough
low-recovery events across training/test windows, acceptable missingness, and representative
behavior before interpreting results. Counts alone do not establish usefulness. Review
genuine walk-forward results, a simple persistence reference, interval coverage, and
prospective errors before trusting forecasts or promoting the challenger.

Phase 5 can build Streamlit and monitoring from `predictions` and MLflow, including clear
empty/insufficient-history states. KMeans/Holt, Grafana, LangChain, Databricks, and hosted
modeling services remain outside the implemented scope.

References: [MLflow backends/registry](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/),
[model persistence](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.sklearn.html),
[time-series validation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html),
[SGD updates](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.SGDRegressor.html),
[WHOOP recovery boundary](https://support.whoop.com/s/article/WHOOP-Recovery?language=en_US).
