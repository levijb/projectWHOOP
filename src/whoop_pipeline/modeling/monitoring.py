"""Pure error-based triggers and bootstrap prediction intervals (not clinical confidence)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray


def retrain_decision(errors: Sequence[float], *, days_since_update: int) -> tuple[bool, str]:
    """Signed predicted-minus-actual errors in 0-100 score POINTS, oldest first.

    Priority is emergency, bias, drift, routine, skip. Drift compares two disjoint seven-day
    MAE windows and requires a >1 point rise to avoid firing on floating-point noise.
    """
    values = np.asarray(errors, dtype=float)
    if days_since_update < 0 or not np.isfinite(values).all():
        raise ValueError("Error history must be finite and elapsed days nonnegative")
    if len(values) and abs(values[-1]) > 15:
        return True, "emergency"
    if len(values) >= 7 and abs(float(np.mean(values[-7:]))) > 5:
        return True, "bias_correction"
    if len(values) >= 14 and np.mean(np.abs(values[-7:])) > np.mean(np.abs(values[-14:-7])) + 1:
        return True, "drift_correction"
    if days_since_update >= 7:
        return True, "routine"
    return False, "stable"


def bootstrap_prediction_intervals(
    predictions: Sequence[float],
    residuals: Sequence[float],
    *,
    confidence: float = 0.90,
    n_bootstrap: int = 1000,
    block_size: int = 7,
    seed: int = 42,
) -> NDArray[np.float64]:
    """Moving-block residual bootstrap from chronological OUT-OF-SAMPLE actual-predicted errors.

    Each bootstrap resamples consecutive residual blocks, retaining short-range dependence.
    Average tail quantiles estimate individual prediction uncertainty, not a CI on the mean.
    Coverage is empirical/approximate, requires calibration on real history, and is clipped
    to the score's [0,100] support. Never pass in-sample fitted residuals here.
    """
    point, errors = np.asarray(predictions, dtype=float), np.asarray(residuals, dtype=float)
    if not 0 < confidence < 1 or n_bootstrap < 100 or block_size < 1:
        raise ValueError("Invalid bootstrap configuration")
    if len(errors) < max(20, block_size) or not np.isfinite(errors).all():
        raise ValueError("Need at least 20 finite out-of-sample residuals")
    if not np.isfinite(point).all() or ((point < 0) | (point > 100)).any():
        raise ValueError("Predictions must be finite and within [0,100]")
    rng = np.random.default_rng(seed)
    starts = rng.integers(
        0, len(errors) - block_size + 1, size=(n_bootstrap, int(np.ceil(len(errors) / block_size)))
    )
    indices = (starts[..., None] + np.arange(block_size)).reshape(n_bootstrap, -1)[:, : len(errors)]
    alpha = (1 - confidence) / 2
    tails = np.quantile(errors[indices], [alpha, 1 - alpha], axis=1).mean(axis=1)
    return np.asarray(np.clip(point[:, None] + tails, 0, 100), dtype=float)
