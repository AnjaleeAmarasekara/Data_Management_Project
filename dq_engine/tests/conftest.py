"""Synthetic clean baseline window for the data quality tests.

The shape here deliberately mirrors production: the loggers report every five minutes and
the scorer works in two-hour windows, so a window holds 24 readings. Testing against a
denser window would let the checks pass on data the system never actually sees -- with 24
readings a single dropped sample is 4% of the window, which is a different problem from a
single dropped sample out of 360.
"""

import numpy as np
import pandas as pd
import pytest

from dq_engine.config import DEFAULT_INTERVAL_S, WINDOW_MINUTES

SAMPLING_INTERVAL_S = DEFAULT_INTERVAL_S
READINGS_PER_WINDOW = int(WINDOW_MINUTES * 60 / SAMPLING_INTERVAL_S)


@pytest.fixture
def clean_window() -> pd.DataFrame:
    n = READINGS_PER_WINDOW
    rng = np.random.default_rng(seed=42)
    ts = pd.date_range("2024-01-01T00:00:00Z", periods=n, freq=f"{int(SAMPLING_INTERVAL_S)}s")
    temp = 4.0 + rng.normal(0, 0.15, n)  # chilled shelf, stable around 4C with small real noise
    humidity = 55.0 + rng.normal(0, 1.0, n)
    return pd.DataFrame({"ts": ts, "temp": temp, "humidity": humidity})
