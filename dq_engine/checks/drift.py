"""Sensor drift detection.

Drift (proposal: "Drift of the Sensor") is a slow, systematic deviation with no single
spike to catch, so it can't be found by the outlier check. It's detected here by
comparing the mean of the first half of the window against the second half, and by the
slope of a linear fit over the whole window -- a window that's outlier-free but still
trending is the drift signature.
"""

import numpy as np
import pandas as pd

from .result import CheckResult


def check_drift(
    window: pd.DataFrame,
    column: str = "temp",
    slope_threshold: float = 0.05,
    shift_threshold: float = 1.5,
) -> CheckResult:
    series = window[column].dropna()
    if len(series) < 10:
        return CheckResult(flag=False, penalty=0.0, evidence={"reason": "insufficient_data"})

    half = len(series) // 2
    mean_shift = float(series.iloc[half:].mean() - series.iloc[:half].mean())

    x = np.arange(len(series))
    slope = float(np.polyfit(x, series.values, 1)[0])

    drifted = abs(slope) > slope_threshold or abs(mean_shift) > shift_threshold
    if not drifted:
        return CheckResult(flag=False, penalty=0.0, evidence={"slope": round(slope, 4), "mean_shift": round(mean_shift, 3)})

    penalty = min(1.0, abs(slope) / slope_threshold * 0.3 + abs(mean_shift) / shift_threshold * 0.3)
    return CheckResult(
        flag=True,
        penalty=penalty,
        evidence={"slope": round(slope, 4), "mean_shift": round(mean_shift, 3)},
    )
