"""Temporal leakage, model capability, monitoring, and anomaly logic on seeded history."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from whoop_pipeline.modeling.anomaly import IsolationAnomalyDetector, RollingAnomalyDetector
from whoop_pipeline.modeling.data import (
    ANOMALY_COLUMNS,
    FEATURE_COLUMNS,
    require_history,
    supervised_data,
)
from whoop_pipeline.modeling.models import RecoveryRegressor, low_recovery_classifier
from whoop_pipeline.modeling.monitoring import bootstrap_prediction_intervals, retrain_decision
from whoop_pipeline.modeling.synthetic import synthetic_history
from whoop_pipeline.modeling.validation import (
    expanding_splits,
    regression_metrics,
    validate_anomaly,
    validate_supervised,
)


@pytest.fixture(scope="module")
def history() -> pd.DataFrame:
    return synthetic_history()


def test_seeded_history_uses_actual_mart_and_is_reproducible(history: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(history, synthetic_history())
    assert not history.equals(synthetic_history(seed=7))
    assert len(history) == 180
    assert history["recovery_score"].autocorr() > 0.2
    assert 0 < (history["recovery_score"] <= 33).mean() < 0.5
    assert set(FEATURE_COLUMNS) <= set(history)
    assert history.iloc[6]["recovery_score_7d_avg"] == pytest.approx(
        history.iloc[:7]["recovery_score"].mean()
    )


def test_labels_are_next_cycle_and_pending_outcomes_do_not_skip_cycles(
    history: pd.DataFrame,
) -> None:
    records = history.iloc[:6].copy()
    records["recovery_score"] = [10.0, 85.0, np.nan, 33.0, 34.0, 90.0]
    data = supervised_data(records.iloc[::-1])
    assert data.target.tolist() == [85.0, 33.0, 34.0, 90.0]
    assert data.low_recovery.tolist() == [0, 1, 0, 0]
    assert data.origins["cycle_id"].tolist() == records.iloc[[0, 2, 3, 4]]["cycle_id"].tolist()
    assert (
        data.origins["target_cycle_id"].tolist() == records.iloc[[1, 3, 4, 5]]["cycle_id"].tolist()
    )
    assert "target_cycle_id" not in data.features
    assert data.features.iloc[0]["recovery_score"] == 10


def test_future_mutations_cannot_change_earlier_features_or_labels(history: pd.DataFrame) -> None:
    changed = history.copy()
    changed.loc[100:, "recovery_score"] = 0
    before, after = supervised_data(history), supervised_data(changed)
    pd.testing.assert_frame_equal(before.features.iloc[:99], after.features.iloc[:99])
    pd.testing.assert_series_equal(before.target.iloc[:99], after.target.iloc[:99])


def test_split_boundaries_are_expanding_and_purged(history: pd.DataFrame) -> None:
    data = supervised_data(history)
    splits = list(expanding_splits(len(data.target)))
    assert len(splits) >= 2
    for train, test in splits:
        assert train[0] == 0
        assert test[0] - train[-1] == 2
        assert (
            data.origins.iloc[train]["target_start_at"].max()
            < data.origins.iloc[test]["start_at"].min()
        )
    assert len(splits[1][0]) > len(splits[0][0])
    with pytest.raises(ValueError, match="two complete"):
        list(expanding_splits(60))


@pytest.mark.parametrize("algorithm", ["ridge", "sgd", "gradient_boosting"])
def test_regressors_validate_train_predict(history: pd.DataFrame, algorithm: str) -> None:
    data = supervised_data(history)
    result = validate_supervised(data, lambda: RecoveryRegressor(algorithm))
    assert len(result.folds) >= 2
    assert {"mae_mean", "mape_mean", "r2_mean"} <= result.aggregate().keys()
    assert np.isfinite(list(result.aggregate().values())).all()
    model = RecoveryRegressor(algorithm).fit(data.features, data.target)
    assert 0 <= model.predict(data.features.iloc[[-1]])[0] <= 100


def test_sgd_partial_fit_changes_weights_without_refitting_preprocessing(
    history: pd.DataFrame,
) -> None:
    data = supervised_data(history)
    model = RecoveryRegressor().fit(data.features.iloc[:60], data.target.iloc[:60])
    before = model.estimator.coef_.copy()
    scale = model.preprocessor.named_steps["scaler"].mean_.copy()
    median = model.preprocessor.named_steps["imputer"].statistics_.copy()
    model.partial_fit(data.features.iloc[60:61], data.target.iloc[60:61])
    assert not np.array_equal(before, model.estimator.coef_)
    np.testing.assert_array_equal(scale, model.preprocessor.named_steps["scaler"].mean_)
    np.testing.assert_array_equal(median, model.preprocessor.named_steps["imputer"].statistics_)
    np.testing.assert_allclose(
        scale,
        model.preprocessor.named_steps["imputer"].transform(data.features.iloc[:60]).mean(axis=0),
    )


def test_classifier_walk_forward_handles_single_class_folds(history: pd.DataFrame) -> None:
    data = supervised_data(history)
    result = validate_supervised(data, low_recovery_classifier, classifier=True)
    assert {"precision_mean", "recall_mean", "auc_mean"} <= result.aggregate().keys()
    classifier = low_recovery_classifier().fit(data.features, data.low_recovery)
    assert classifier.predict_proba(data.features.iloc[:3]).shape == (3, 2)
    history = history.copy()
    history["recovery_score"] = 75
    single_class = validate_supervised(
        supervised_data(history), low_recovery_classifier, classifier=True
    )
    assert not single_class.folds and single_class.skipped_folds > 0


@pytest.mark.parametrize(
    ("errors", "days", "expected"),
    [
        ([16], 7, (True, "emergency")),
        ([-16], 0, (True, "emergency")),
        ([15], 0, (False, "stable")),
        ([6] * 7, 8, (True, "bias_correction")),
        ([-6] * 7, 0, (True, "bias_correction")),
        ([5] * 7, 0, (False, "stable")),
        ([1, -1, 1, -1, 1, -1, 1, 4, -4, 4, -4, 4, -4, 4], 0, (True, "drift_correction")),
        ([1] * 14, 7, (True, "routine")),
        ([], 7, (True, "routine")),
        ([1] * 14, 6, (False, "stable")),
    ],
)
def test_retrain_trigger_priority_and_boundaries(
    errors: list[float], days: int, expected: tuple[bool, str]
) -> None:
    assert retrain_decision(errors, days_since_update=days) == expected


def test_metrics_and_bootstrap_handle_zero_targets_bounds_and_reproducibility() -> None:
    metrics = regression_metrics(np.array([0.0, 10.0, 20.0]), np.array([5.0, 12.0, 18.0]))
    assert metrics["mape"] == pytest.approx(15)
    assert metrics["mape_excluded_zeros"] == 1
    residuals = np.linspace(-10, 10, 60)
    intervals = bootstrap_prediction_intervals([1, 50, 99], residuals)
    np.testing.assert_array_equal(intervals, bootstrap_prediction_intervals([1, 50, 99], residuals))
    assert intervals.shape == (3, 2)
    assert (intervals[:, 0] <= intervals[:, 1]).all()
    assert (intervals >= 0).all() and (intervals <= 100).all()
    assert intervals[1, 0] < 50 < intervals[1, 1]
    with pytest.raises(ValueError, match="20"):
        bootstrap_prediction_intervals([50], [1, 2])
    with pytest.raises(ValueError, match="finite"):
        retrain_decision([float("nan")], days_since_update=0)


@pytest.mark.parametrize("factory", [RollingAnomalyDetector, IsolationAnomalyDetector])
def test_anomaly_flags_injected_outlier_and_not_baseline(factory: type) -> None:
    rng = np.random.default_rng(12)
    normal = np.array([65.0, 56.0, 33.4, 15.0])
    noise = rng.normal(size=(120, 4)) * np.array([1.0, 0.5, 0.03, 0.06])
    baseline = pd.DataFrame(normal + noise, columns=list(ANOMALY_COLUMNS))
    model = factory().fit(baseline)
    checks = pd.DataFrame([normal, [10.0, 110.0, 37.0, 24.0]], columns=list(ANOMALY_COLUMNS))
    assert model.predict(checks).tolist() == [False, True]
    result = validate_anomaly(baseline, factory)
    assert len(result.folds) >= 2
    assert "flag_rate_mean" in result.aggregate()


def test_sparse_missing_and_multiuser_data_are_explicit(history: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="90 labeled"):
        require_history(supervised_data(history.iloc[:2]))
    with pytest.raises(ValueError, match="single user"):
        supervised_data(pd.concat([history, history.assign(user_id=123)]))
    detector = RollingAnomalyDetector().fit(history.iloc[:5])
    scores = detector.score(history.iloc[[5]])
    assert scores.isna().all().all()  # unavailable baseline, not evidence of normal health


@pytest.mark.parametrize("days", [60, 120, 240])
def test_synthetic_history_sizes_and_initial_window_readiness(days: int) -> None:
    data = supervised_data(synthetic_history(days))
    assert len(data.target) == days - 1
    if days == 60:
        with pytest.raises(ValueError, match="90 labeled"):
            require_history(data)
    else:
        require_history(data)
        assert len(list(expanding_splits(len(data.target)))) >= 2
