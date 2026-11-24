"""Missing-data / gap detection.

Flags a window when the observed interval between consecutive readings is
significantly larger than the device's expected sampling interval -- i.e. readings
that should have arrived didn't, most often caused by network dropouts or device
resets (see proposal: "Communication and Network Problems", "Incomplete Records").
"""

import pandas as pd

from .result import CheckResult


def check_missing(
    window: pd.DataFrame,
    expected_interval_s: float,
    gap_multiplier: float = 3.0,
    min_ratio: float = 0.02,
    window_start: pd.Timestamp | None = None,
    window_end: pd.Timestamp | None = None,
) -> CheckResult:
    """window must have a 'ts' column of tz-aware datetimes, sorted ascending.

    window_start / window_end are the bounds the window was *supposed* to cover. Without
    them only the gaps between readings can be seen, so an outage at the edge of a window -
    the sensor going quiet at minute 40 and staying quiet - is invisible: the last reading
    is followed by nothing, so there is no gap to measure. Measured on real data, a 3-hour
    outage spanning a window boundary and a 6-hour outage covering whole windows both
    produced no missing-data flag at all before the bounds were taken into account.
    """
    threshold = expected_interval_s * gap_multiplier
    span_s = (window_end - window_start).total_seconds() if window_start is not None and window_end is not None else None

    if len(window) < 2 and span_s is None:
        return CheckResult(flag=True, penalty=1.0, evidence={"reason": "insufficient_data", "rows": len(window)})

    gaps = window["ts"].diff().dt.total_seconds().dropna()
    big_gaps = gaps[gaps > threshold]
    gap_seconds = list(big_gaps.values)
    flagged = window.loc[big_gaps.index, "ts"].tolist()

    # the silence before the first reading and after the last, measured against the bounds
    if span_s is not None:
        if len(window):
            lead = (window["ts"].iloc[0] - window_start).total_seconds()
            trail = (window_end - window["ts"].iloc[-1]).total_seconds()
        else:
            lead, trail = span_s, 0.0
        for edge in (lead, trail):
            if edge > threshold:
                gap_seconds.append(edge)

    if not gap_seconds:
        return CheckResult(flag=False, penalty=0.0, evidence={"max_gap_s": round(float(gaps.max()), 2) if len(gaps) else 0.0})

    estimated_missing = int(sum(g / expected_interval_s for g in gap_seconds)) - len(gap_seconds)
    if span_s is not None:
        total_expected = max(int(span_s / expected_interval_s), len(window))
        estimated_missing = min(max(estimated_missing, total_expected - len(window)), total_expected)
    else:
        total_expected = len(window) + estimated_missing
    missing_ratio = estimated_missing / total_expected if total_expected else 1.0

    if missing_ratio < min_ratio:
        # Real devices jitter; one late reading is not a data-quality problem.
        return CheckResult(flag=False, penalty=0.0, evidence={"missing_ratio": round(missing_ratio, 3)})

    return CheckResult(
        flag=True,
        penalty=min(1.0, missing_ratio * 4),
        evidence={
            "gap_count": len(gap_seconds),
            "estimated_missing_readings": estimated_missing,
            "missing_ratio": round(missing_ratio, 3),
        },
        flagged_timestamps=flagged,
    )
