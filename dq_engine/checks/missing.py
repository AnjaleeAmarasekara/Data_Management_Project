"""Missing-data / gap detection.

Flags a window when the observed interval between consecutive readings is
significantly larger than the device's expected sampling interval -- i.e. readings
that should have arrived didn't, most often caused by network dropouts or device
resets (see proposal: "Communication and Network Problems", "Incomplete Records").
"""

import pandas as pd

from .result import CheckResult


def check_missing(
    window: pd.DataFrame, expected_interval_s: float, gap_multiplier: float = 3.0, min_ratio: float = 0.02
) -> CheckResult:
    """window must have a 'ts' column of tz-aware datetimes, sorted ascending."""
    if len(window) < 2:
        return CheckResult(flag=True, penalty=1.0, evidence={"reason": "insufficient_data", "rows": len(window)})

    gaps = window["ts"].diff().dt.total_seconds().dropna()
    threshold = expected_interval_s * gap_multiplier
    big_gaps = gaps[gaps > threshold]

    if big_gaps.empty:
        return CheckResult(flag=False, penalty=0.0, evidence={"max_gap_s": round(float(gaps.max()), 2)})

    estimated_missing = int((big_gaps / expected_interval_s).sum()) - len(big_gaps)
    total_expected = len(window) + estimated_missing
    missing_ratio = estimated_missing / total_expected if total_expected else 0.0

    if missing_ratio < min_ratio:
        # Real devices jitter; one late reading is not a data-quality problem.
        return CheckResult(flag=False, penalty=0.0, evidence={"missing_ratio": round(missing_ratio, 3)})

    return CheckResult(
        flag=True,
        penalty=min(1.0, missing_ratio * 4),
        evidence={
            "gap_count": len(big_gaps),
            "estimated_missing_readings": estimated_missing,
            "missing_ratio": round(missing_ratio, 3),
        },
        flagged_timestamps=window.loc[big_gaps.index, "ts"].tolist(),
    )
