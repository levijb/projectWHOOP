# Phase 4 — modeling suite and prediction serving

## Outcome and data limitation

Implemented the modeling capability, local MLflow registry, and guarded serving path.
**Real accumulated history was only two rows at handoff. No model was trained on real
history, and no synthetic model was promoted for real use.** The successful Supabase pull
and no-duplication rerun were reported by the operator; no live verification was repeated.

The default experiment used **180 seeded synthetic days**, 179 next-cycle labels, and
38 low-recovery labels. It runs the repository's actual mart SQL. Generated data is a noisy,
autocorrelated simulation, not real wearable measurements or clinically calibrated physiology.

## Built

- Shared expanding validation: initial 60 training pairs, a one-pair boundary gap, 14-pair
  future test windows; training-only imputation/scaling and an explicit label-time assertion.
- Ridge batch baseline and SGDRegressor incremental candidate, with `partial_fit` retaining
  fitted preprocessing. A scikit-learn gradient-boosting challenger is available but not
  promoted. This fulfills the gradient-boosting upgrade path without an XGBoost dependency.
- Balanced logistic regression for **next-cycle** recovery <=33. Labels shift before pending
  outcomes are dropped; tests prove N uses N+1 and never skips a pending cycle.
- Prior-only median/MAD anomaly detection on four physiological inputs, plus Isolation Forest.
  Injected-outlier tests check behavior, not fictional illness-detection accuracy.
- Pure prioritized retrain decisions: latest error >15 points; absolute last-7 bias >5;
  last-7 MAE >previous-7 MAE+1; seven elapsed days; otherwise skip. The 1-point drift margin
  is the explicit interpretation of the spec's unquantified upward trend. Error windows count
  observed forecast cycles; routine timing uses calendar days.
- Separate moving-block bootstrap prediction intervals using out-of-sample residuals:
  7-residual blocks, 1,000 resamples, 90% intervals, bounded to 0-100.
- Local MLflow SQLite experiment tracking **and model registry**, with per-fold/aggregate
  metrics, parameters, model artifacts, versioning, and a round-trip reload test. Synthetic
  runs set no active alias. Ambient remote tracking/registry URLs are ignored.
- New Alembic **0002** for `whoop.predictions`, plus transactional SQLite-substitute tests.
  It stores the origin cycle, model name/version, next-cycle ID once known, timestamps,
  prediction/interval, and eventual actual/signed error. Retries preserve the issued forecast.
- `daily_model_update`, downstream of the mart, settles errors, decides whether to update,
  versions SGD, and stores the next prediction. Its separate Dagster job is disabled by
  default; both manual resource switches and existing Postgres opt-in protect real use.
- Readiness, stale-data, single-user, retry, database rollback, failed-insert recovery, and
  persistent model reload checks. A local file lock prevents overlapping Dagster model updates
  using the same registry directory.

The existing scheduled ingestion job, workflow files, secrets, and applied **0001 migration
were not changed**. The only feature SQL change normalizes DuckDB day-of-week extraction to
UTC, matching Postgres. A synthetic test compares the harness with an actual dbt mart build.

## Confirmed feature columns and judgment calls

Both supervised models use these exact columns from the existing mart:

```text
day_of_week, cycle_strain, recovery_score, hrv_rmssd_milli,
sleep_debt_need_hours, recovery_score_7d_avg, cycle_strain_7d_avg,
hrv_rmssd_milli_7d_avg, prior_day_strain, sleep_debt_7d_avg_hours,
sleep_debt_trend_hours, days_since_last_low_strain_day
```

The mart already includes recovery_score. Current-cycle features predict **the immediate
next observed cycle**, approximately tomorrow, for both regression and classification; this
avoids same-cycle target leakage from recovery and its current-inclusive rolling average.
IDs/timestamps are excluded. Day-of-week remains a numeric baseline input. Missing features
are imputed within training only. Missing targets are never invented or imputed.

Low recovery is **<=33**, following [WHOOP's red-zone convention](https://support.whoop.com/s/article/WHOOP-Recovery?language=en_US).
This is an initial modeling boundary, not a clinical cutoff or settled personal threshold.
Anomalies use HRV plus `resting_heart_rate`, `skin_temp_celsius`, and `respiratory_rate` joined
from gold recovery/main sleep. Skin-temperature deviation is calculated from the person's
prior baseline; SpO2 is available but not used in the initial four-field detector.

## Synthetic walk-forward results — not real-world performance

Seed **42**, 180 days, **8 folds** with 14 future rows each (112 out-of-sample residuals).
Values below are means across folds; full standard deviations and counts are retained in
the local JSON/MLflow runs.

| Regression variant | MAE, score points | MAPE, % of actual | Mean R² |
|---|---:|---:|---:|
| Ridge | 9.295 | 43.081 | 0.281 |
| SGD | 9.384 | 41.301 | 0.273 |
| Gradient boosting challenger | 10.260 | 34.022 | 0.187 |

Ridge/SGD MAE standard deviations were 1.449/1.519 points. High MAPE reflects the sensitivity
to very low synthetic recovery scores; zero actuals would be excluded and counted explicitly.

Logistic precision **0.369**, recall **0.288**, AUC **0.817**. Precision/recall average all eight
folds with zero for undefined cases. AUC is defined in only **five** folds; the remaining
three contain a single test class. These counts matter: the AUC does not establish useful
real-day recall or readiness for deployment.

Anomaly flag rates were **5.36%** (robust baseline) and **3.57%** (Isolation Forest) across eight
future windows. They are rates of deviation flags, **not illness/overtraining accuracy**.

The final synthetic SGD next-cycle example was **45.62**, with approximate 90% prediction
interval **[30.23, 66.44]**. This is not a forecast for the user. Fixture benchmarks do not
represent real small-N behavior, so the intended SGD candidate remains a design choice and
gradient boosting remains gated on a later genuine walk-forward comparison.

## Verification

| Check | Result |
|---|---|
| Full pytest suite, optional embedded PostgreSQL runtime enabled | **131 passed** |
| Coverage | **86%** overall |
| Ruff lint and formatting check | Passed |
| mypy | Passed, 34 source files |
| Editable install with `.[dev]` | Passed, including modeling extras |
| Real dbt mart build on 180 synthetic rows | Model and all five selected mart tests passed; output matches generator |
| SQLite migration/schema/upserts/outcome settlement | Passed |
| Embedded PostgreSQL engine | All three existing regression scenarios passed with 0002 included |
| MLflow demo | Six local registered models; no real-model activation |

Without the optional PGlite runtime, the Python-only suite skips its three existing engine
tests. Existing live-resource guard tests remain in place. The full run reported seven
warnings: the existing requests dependency warning, three pandas 2.x downcasting warnings
in existing transforms, and three dagster-dbt context deprecations. They are documented,
not suppressed. No remote integration was substituted for the offline checks.

## Safety and remaining limits

- No live WHOOP/Postgres connection, OAuth flow, signup, Docker operation, workflow dispatch,
  secret change, push, or real `.env` read/write. Dependencies were installed from package
  registries; training/testing stayed local. Work used the canonical Personal/Code checkout.
- MLflow 3.15.2, scikit-learn 1.6.1, numpy 1.26.2, and pandas 2.3.3 were used. MLflow's pandas
  <3 requirement moved this interpreter from pandas 3.0.2 to 2.3.3. pip also reported an
  unrelated existing radiomicviz/pyradiomics dependency issue; unrelated packages were not repaired.
- New snapshots log a **fresh-fit walk-forward reference**, explicitly tagged, rather than
  claiming those scores measure the exact online snapshot. Prospective SQL forecasts provide
  the future observed errors. Partial updates have no guaranteed improvement.
- Mutable historical features cannot reconstruct what was available at a prior forecast time.
  Open-cycle strain and corrected scores require prospective evaluation at a consistent time.
- Bootstrap coverage and anomaly thresholds are not clinically validated. An unavailable
  baseline or absence of a flag must not be interpreted as evidence of health.
- MLflow artifacts use trusted-local cloudpickle; do not load untrusted models. MLflow/SQL are
  separate stores, not a distributed transaction. A crash before activation may leave an unused
  model version; distinct hosts/directories still require operator-enforced single-writer use.
- No real migration 0002, Docker verification, or real model activation was attempted.
- Optional KMeans archetypes/Holt forecasting were deferred to keep scope on the three
  requested model families and serving safety. Dashboard/Grafana/LangChain/Databricks remain deferred.

## Next steps

Run `whoop-model-demo` and `mlflow ui` as shown in [docs/modeling.md](docs/modeling.md) to
inspect the local synthetic runs. Artifacts/results are under Git-ignored `.modeling/synthetic`.

When ready, manually apply **0002** using the existing gated `alembic upgrade head` path.
Collect **90 labeled next-cycle pairs spanning at least 90 days** before the code permits
initial training; **120-180+ days**, class diversity, acceptable missingness, and genuine
walk-forward/prospective checks are a better basis for evaluation. Real training remains
**manual and deliberate**, never enabled by accumulating enough rows alone.

Phase 5 is the **Streamlit dashboard and monitoring layer**, consuming `predictions` and
MLflow metrics/versions, with explicit untrained/insufficient-history states. The detailed
operator checklist is in [NEXT_STEPS_FOR_HUMAN.md](NEXT_STEPS_FOR_HUMAN.md).
