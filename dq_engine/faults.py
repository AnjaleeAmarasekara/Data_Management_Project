"""Synthetic fault injection.

Used by the test suite (the proposal's risk mitigation: test cases for drift, outliers,
network loss and calibration error) and by the dashboard's fault-injection demo, which
corrupts a copy of a real window in memory and shows the quality layer reacting.
"""

import numpy as np
import pandas as pd


def drop_rows(df: pd.DataFrame, start: int, count: int) -> pd.DataFrame:
    """Simulate a communication dropout: remove a contiguous block of readings."""
    return df.drop(df.index[start : start + count]).reset_index(drop=True)


def inject_spike(df: pd.DataFrame, at: int, magnitude: float) -> pd.DataFrame:
    """Simulate a single interference spike at one reading."""
    out = df.copy()
    out.loc[at, "temp"] += magnitude
    return out


def inject_drift(df: pd.DataFrame, slope_per_reading: float) -> pd.DataFrame:
    """Simulate gradual sensor drift: a linear ramp added across the window."""
    out = df.copy()
    out["temp"] = out["temp"] + np.arange(len(out)) * slope_per_reading
    return out


def inject_calibration_bias(df: pd.DataFrame, offset: float) -> pd.DataFrame:
    """Simulate a constant calibration error: every reading offset by a fixed amount."""
    out = df.copy()
    out["temp"] = out["temp"] + offset
    return out


def inject_noise(df: pd.DataFrame, magnitude: float, fraction: float = 0.3) -> pd.DataFrame:
    """Simulate electrical interference: random large jumps on a fraction of readings."""
    out = df.copy()
    rng = np.random.default_rng(seed=7)
    mask = rng.random(len(out)) < fraction
    out.loc[mask, "temp"] += rng.choice([-1, 1], size=mask.sum()) * magnitude
    return out


def inject_duplicate_timestamp(df: pd.DataFrame, at: int) -> pd.DataFrame:
    out = df.copy()
    out.loc[at, "ts"] = out.loc[at - 1, "ts"]
    return out


def inject_replay(df: pd.DataFrame, start: int, count: int) -> pd.DataFrame:
    """Simulate a replayed/frozen signal: a block of readings all pinned to one exact
    value, as a static or looped injected signal would look (FDIA-style tampering)."""
    out = df.copy()
    frozen_value = out.loc[start, "temp"]
    out.loc[start : start + count - 1, "temp"] = frozen_value
    return out


def inject_impossible_jump(df: pd.DataFrame, at: int, magnitude: float = 20.0) -> pd.DataFrame:
    """Simulate an injected reading that jumps further than the physical system could
    move in one sample -- a signature of fabricated rather than measured data."""
    out = df.copy()
    out.loc[at, "temp"] += magnitude
    return out
