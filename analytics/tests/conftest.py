"""Fixtures for the analytics tests: a unit's clean, trust-tagged series on the real
five-minute grid, with a temperature profile the test controls exactly."""

import pandas as pd
import pytest

from analytics.config import STEP_MINUTES, UnitConfig


def make_series(temps, trust=100.0, day=1, humidity=50.0) -> pd.DataFrame:
    ts = pd.date_range("2017-08-01T00:00:00Z", periods=len(temps), freq=f"{STEP_MINUTES}min")
    return pd.DataFrame(
        {"ts": ts, "cold_temp": list(map(float, temps)), "humidity": humidity, "trust": trust, "relative_day": day}
    )


@pytest.fixture
def chiller() -> UnitConfig:
    return UnitConfig("Test Chiller", 4.0, 5.0, ("Dairy",), min_safe_c=0.0)


@pytest.fixture
def freezer() -> UnitConfig:
    return UnitConfig("Test Freezer", -18.0, -15.0, ("Frozen Foods",))
