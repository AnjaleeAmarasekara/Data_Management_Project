"""Outlier / calibration-error detection.

Two layers, both from the proposal's "Errors in Calibration" and general reliability
discussion: (1) physically implausible absolute values, which indicate a sensor fault
or gross calibration bias, and (2) statistical outliers relative to a local rolling
baseline (IQR on the detrended residual), which catch spikes that don't reflect a real
environmental change.

The statistical layer detrends first (subtracts a rolling median) rather than taking
IQR bounds on the raw series, because a drifting window has naturally wider raw-value
spread -- computing IQR directly on raw values would let drift mask a genuine spike by
inflating the "normal" range. Detrending keeps outlier detection sensitive regardless of
whatever slow trend the drift check is separately responsible for catching.
"""

import pandas as pd

from .result import CheckResult

PLAUSIBLE_RANGES = {
    "temp": (-30.0, 50.0),  # deg C -- generous bound spanning freezer through ambient
    "humidity": (0.0, 100.0),
}

# Floor on the residual spread. Sensors report in coarse steps (0.1C), so the true IQR of
# the detrended series is often exactly 0 -- without a floor, every non-zero residual
# would be flagged (measured: ~10% of real readings falsely flagged before this floor).
MIN_IQR = {"temp": 0.3, "humidity": 1.5}


def check_outliers(window: pd.DataFrame, column: str = "temp", iqr_multiplier: float = 3.0) -> CheckResult:
    series = window[column].dropna()
    if series.empty:
        return CheckResult(flag=False, penalty=0.0, evidence={"reason": "no_data"})

    lo, hi = PLAUSIBLE_RANGES.get(column, (series.min(), series.max()))
    implausible = series[(series < lo) | (series > hi)]

    baseline = series.rolling(window=15, center=True, min_periods=1).median()
    residual = series - baseline

    q1, q3 = residual.quantile(0.25), residual.quantile(0.75)
    iqr = max(q3 - q1, MIN_IQR.get(column, 0.0))
    stat_lo, stat_hi = q1 - iqr_multiplier * iqr, q3 + iqr_multiplier * iqr
    statistical = residual[(residual < stat_lo) | (residual > stat_hi)]

    flagged_idx = implausible.index.union(statistical.index)
    if flagged_idx.empty:
        return CheckResult(flag=False, penalty=0.0, evidence={"iqr_bounds": [round(float(stat_lo), 2), round(float(stat_hi), 2)]})

    ratio = len(flagged_idx) / len(series)
    return CheckResult(
        flag=True,
        penalty=min(1.0, ratio * 3 + (0.3 if not implausible.empty else 0.0)),
        evidence={
            "implausible_count": int(len(implausible)),
            "statistical_outlier_count": int(len(statistical)),
            "outlier_ratio": round(ratio, 3),
        },
        flagged_timestamps=window.loc[flagged_idx, "ts"].tolist(),
    )
