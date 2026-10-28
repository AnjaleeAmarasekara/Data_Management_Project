"""Integrity checks -- distinguishing a spoofed/tampered reading from a merely faulty one.

The other checks (missing, outlier, drift, noise) answer "is this sensor malfunctioning?"
These two answer a different question: "does this data look like it was manipulated
rather than measured?" That's the same statistical territory as anomaly-based intrusion
detection on IoT/industrial sensor networks -- the literature calls the attack class a
False Data Injection Attack (FDIA). Two FDIA signatures are detectable per-window without
needing a second sensor to cross-check against:

- A replayed/frozen value: real sensors have thermal and electrical noise, so exact
  repeats across many consecutive readings are a signature of a static or looped
  injected value, not a real (if stable) environment.
- A physically impossible jump: a change bigger than the thermal mass of a cold-storage
  unit could produce in one sampling interval, regardless of what's "statistically
  unusual" for that window (the outlier check's job) -- this is an absolute physical
  bound, not a relative one.

Neither check can catch a *slow*, gradual injection -- that is indistinguishable from
drift using single-sensor statistics, the same documented blind spot as calibration bias
(see checks/outlier.py and docs/data_linkage.md). Named here rather than hidden.
"""

import pandas as pd

from .result import CheckResult

MAX_PHYSICAL_JUMP_C = 15.0  # no real cold-storage unit's sensor reading can move this far in one sample


def check_replay(window: pd.DataFrame, column: str = "temp", min_run: int = 6, min_ratio: float = 0.08) -> CheckResult:
    """Flags runs of exactly-repeated consecutive values -- a static/looped/replayed signal."""
    series = window[column].dropna()
    if len(series) < min_run:
        return CheckResult(flag=False, penalty=0.0, evidence={"reason": "insufficient_data"})

    same_as_prev = series.diff().eq(0)
    run_id = (~same_as_prev).cumsum()
    run_lengths = same_as_prev.groupby(run_id).transform("sum") + 1
    frozen = run_lengths >= min_run

    frozen_ratio = frozen.sum() / len(series)
    if frozen_ratio < min_ratio:
        return CheckResult(flag=False, penalty=0.0, evidence={"frozen_ratio": round(float(frozen_ratio), 3)})

    return CheckResult(
        flag=True,
        penalty=min(1.0, frozen_ratio * 2),
        evidence={"frozen_ratio": round(float(frozen_ratio), 3), "longest_run": int(run_lengths.max())},
        flagged_timestamps=window.loc[frozen[frozen].index, "ts"].tolist(),
    )


def check_impossible_jump(window: pd.DataFrame, column: str = "temp", max_jump: float = MAX_PHYSICAL_JUMP_C) -> CheckResult:
    """Flags a single-step change bigger than the physical system could produce."""
    series = window[column].dropna()
    if len(series) < 2:
        return CheckResult(flag=False, penalty=0.0, evidence={"reason": "insufficient_data"})

    deltas = series.diff().abs()
    impossible = deltas[deltas > max_jump]
    if impossible.empty:
        return CheckResult(flag=False, penalty=0.0, evidence={"max_delta": round(float(deltas.max()), 2)})

    return CheckResult(
        flag=True,
        penalty=1.0,  # a physically impossible reading is never partially trustworthy
        evidence={"impossible_count": int(len(impossible)), "max_delta": round(float(impossible.max()), 2)},
        flagged_timestamps=window.loc[impossible.index, "ts"].tolist(),
    )
