"""Synthetic anomaly injectors for evaluating the Isolation Forest detector.

Same discipline as dq_engine/faults.py (inject a known fault, confirm the detector
reacts) applied to the ML side -- the standard way an unsupervised anomaly detector is
evaluated when no real incident log exists to supply ground truth.
"""

import numpy as np
import pandas as pd


def inject_temp_bump(df: pd.DataFrame, start: int, duration: int, magnitude: float, max_safe_c: float) -> tuple[pd.DataFrame, set]:
    """A sustained temperature rise that stays under the safe limit -- the case the
    rule-based excursion check cannot catch by definition, so only the anomaly detector
    has a chance at it. Returns the corrupted copy and the true-positive row positions."""
    out = df.reset_index(drop=True).copy()
    end = min(start + duration, len(out))
    headroom = max(0.3, (max_safe_c - 0.3) - float(out.loc[start, "cold_temp"]))
    bump = min(magnitude, headroom)
    out.loc[start : end - 1, "cold_temp"] = out.loc[start : end - 1, "cold_temp"] + bump
    return out, set(range(start, end))


def inject_humidity_anomaly(df: pd.DataFrame, start: int, duration: int, magnitude: float) -> tuple[pd.DataFrame, set]:
    """An odd humidity swing with no matching temperature change -- a combination a
    single-variable temperature threshold could never see, but a multivariate detector
    should. Returns the corrupted copy and the true-positive row positions."""
    out = df.reset_index(drop=True).copy()
    end = min(start + duration, len(out))
    out.loc[start : end - 1, "humidity"] = out.loc[start : end - 1, "humidity"] + magnitude
    return out, set(range(start, end))


def random_injection_plan(rng: np.random.Generator, n_rows: int, n_injections: int, min_gap: int = 60) -> list[tuple[int, int]]:
    """Non-overlapping (start, duration) spots scattered through a series, kept well
    separated so injected events don't merge into one and so one series can host several
    independent trials' worth of signal without them interfering."""
    spots: list[tuple[int, int]] = []
    attempts = 0
    while len(spots) < n_injections and attempts < 1000:
        attempts += 1
        start = int(rng.integers(0, max(1, n_rows - 20)))
        duration = int(rng.integers(3, 12))
        if all(abs(start - s) > min_gap for s, _ in spots):
            spots.append((start, duration))
    return spots
