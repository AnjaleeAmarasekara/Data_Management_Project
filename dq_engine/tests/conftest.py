"""Synthetic clean baseline window for the data quality tests."""

import numpy as np
import pandas as pd
import pytest

SAMPLING_INTERVAL_S = 5.0
WINDOW_MINUTES = 30


@pytest.fixture
def clean_window() -> pd.DataFrame:
    n = int(WINDOW_MINUTES * 60 / SAMPLING_INTERVAL_S)
    rng = np.random.default_rng(seed=42)
    ts = pd.date_range("2024-01-01T00:00:00Z", periods=n, freq=f"{int(SAMPLING_INTERVAL_S)}s")
    temp = 4.0 + rng.normal(0, 0.15, n)  # fridge unit, stable around 4C with small real noise
    humidity = 55.0 + rng.normal(0, 1.0, n)
    return pd.DataFrame({"ts": ts, "temp": temp, "humidity": humidity})
