"""Noise and communication-integrity checks.

- Rate-of-change (proposal: "Environmental Interference and Noise"): flags rapid
  oscillation between consecutive readings that's too fast to be a real environmental
  change, distinct from drift (slow, systematic) and outliers (isolated spikes).
- Timestamp integrity: duplicate or out-of-order timestamps, a symptom of network
  retransmission/buffering issues (proposal: "Communication and Network Problems").
"""

import pandas as pd

from .result import CheckResult


def check_noise(
    window: pd.DataFrame, column: str = "temp", rate_threshold: float = 2.0, min_ratio: float = 0.02
) -> CheckResult:
    series = window[column].dropna()
    if len(series) < 3:
        return CheckResult(flag=False, penalty=0.0, evidence={"reason": "insufficient_data"})

    deltas = series.diff().abs().dropna()
    noisy = deltas[deltas > rate_threshold]
    oscillation_ratio = len(noisy) / len(deltas)

    # An isolated spike makes two big transitions (in and out) but is the outlier check's job;
    # noise means a sustained fraction of readings jumping around.
    if oscillation_ratio < min_ratio:
        return CheckResult(flag=False, penalty=0.0, evidence={"max_delta": round(float(deltas.max()), 3)})

    return CheckResult(
        flag=True,
        penalty=min(1.0, oscillation_ratio * 4),
        evidence={"noisy_transitions": int(len(noisy)), "oscillation_ratio": round(oscillation_ratio, 3)},
        flagged_timestamps=window.loc[noisy.index, "ts"].tolist(),
    )


def check_timestamp_integrity(window: pd.DataFrame) -> CheckResult:
    ts = window["ts"]
    duplicates = ts[ts.duplicated()]
    out_of_order = int((ts.diff().dt.total_seconds() < 0).sum())

    if duplicates.empty and out_of_order == 0:
        return CheckResult(flag=False, penalty=0.0, evidence={})

    penalty = min(1.0, (len(duplicates) + out_of_order) / max(len(ts), 1))
    return CheckResult(
        flag=True,
        penalty=penalty,
        evidence={"duplicate_count": int(len(duplicates)), "out_of_order_count": out_of_order},
    )
