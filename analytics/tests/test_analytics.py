"""Tests for the mining layer. Until these existed every test in the project covered the
quality engine only, which is how adding a new event kind could break the alerts page
without anything failing."""

import pandas as pd
import pytest

from analytics import anomaly
from analytics.anomaly import detect_cold_excursions, detect_excursions, detect_isolation_forest, detect_silences
from analytics.business_impact import estimate_impact
from analytics.config import MIN_DAY_COVERAGE_STEPS, STEP_MINUTES
from analytics.forecast import forecast_trend

from .conftest import make_series

FULL_DAY = 288  # samples in a day at five-minute spacing


class TestExcursions:
    def test_warm_excursion_is_one_event_with_true_duration(self, chiller):
        temps = [4.0] * 10 + [7.0] * 6 + [4.0] * 10
        events = detect_excursions(make_series(temps), chiller.max_safe_c)
        assert len(events) == 1
        assert events[0].minutes == 6 * STEP_MINUTES  # durations are real minutes, not rows
        assert events[0].peak_temp_c == 7.0

    def test_cold_excursion_is_detected_and_reports_the_low(self, chiller):
        temps = [4.0] * 10 + [-1.0, -4.0, -2.0] + [4.0] * 10
        events = detect_cold_excursions(make_series(temps), chiller.min_safe_c)
        assert len(events) == 1
        assert events[0].kind == "cold_excursion"
        assert events[0].peak_temp_c == -4.0  # for a cold event the stored extreme is the low
        assert events[0].severity == "critical"  # 3 C or more below the bound

    def test_a_freezer_has_no_lower_bound(self, freezer):
        events = detect_cold_excursions(make_series([-25.0] * 30), freezer.min_safe_c)
        assert events == []


class TestIsolationForestDeduplication:
    """An early warning must never repeat minutes already reported as an excursion."""

    def _flag_all(self, monkeypatch):
        monkeypatch.setattr(anomaly, "isolation_forest_flags", lambda df: pd.Series(True, index=df.index))

    def test_skips_events_already_reported_as_too_cold(self, monkeypatch, chiller):
        self._flag_all(monkeypatch)
        df = make_series([-2.0] * 5)
        assert detect_isolation_forest(df, chiller.max_safe_c, chiller.min_safe_c) == []

    def test_skips_events_already_reported_as_too_warm(self, monkeypatch, chiller):
        self._flag_all(monkeypatch)
        df = make_series([9.0] * 5)
        assert detect_isolation_forest(df, chiller.max_safe_c, chiller.min_safe_c) == []

    def test_keeps_an_anomaly_inside_the_safe_band(self, monkeypatch, chiller):
        self._flag_all(monkeypatch)
        df = make_series([3.0] * 5)
        assert len(detect_isolation_forest(df, chiller.max_safe_c, chiller.min_safe_c)) == 1


class TestBusinessImpact:
    def test_freezing_counts_as_risk(self, chiller):
        df = make_series([-2.0] * FULL_DAY)  # a whole day 2 C below the lower bound
        row = estimate_impact(df, chiller, {1: {"Dairy": 1000.0}})[0]
        assert row["cold_minutes"] == FULL_DAY * STEP_MINUTES
        assert row["cold_degree_minutes"] == pytest.approx(2.0 * FULL_DAY * STEP_MINUTES)
        assert row["revenue_at_risk"] > 0

    def test_a_unit_inside_its_band_carries_no_risk(self, chiller):
        row = estimate_impact(make_series([3.0] * FULL_DAY), chiller, {1: {"Dairy": 1000.0}})[0]
        assert row["revenue_at_risk"] == 0
        assert row["degree_minutes"] == 0

    def test_degree_minutes_use_real_time_not_rows(self, chiller):
        df = make_series([6.0] * 12 + [4.0] * (FULL_DAY - 12))  # 1 C over for one hour
        row = estimate_impact(df, chiller, {1: {"Dairy": 1000.0}})[0]
        assert row["degree_minutes"] == pytest.approx(60.0)

    def test_product_temperature_replaces_air_and_is_recorded(self, chiller):
        air = make_series([7.0] * FULL_DAY)          # air says breach all day
        product = make_series([4.5] * FULL_DAY)      # the goods themselves never breached
        row = estimate_impact(air, chiller, {1: {"Dairy": 1000.0}}, product_df=product)[0]
        assert row["temp_basis"] == "product"
        assert row["revenue_at_risk"] == 0

    def test_without_a_probe_the_basis_is_air(self, chiller):
        row = estimate_impact(make_series([3.0] * FULL_DAY), chiller, {1: {}})[0]
        assert row["temp_basis"] == "air"

    def test_a_day_with_too_little_data_is_not_scored(self, chiller):
        assert estimate_impact(make_series([7.0] * (MIN_DAY_COVERAGE_STEPS - 1)), chiller, {}) == []


class TestForecastDirection:
    def test_warming_trend_warns_of_the_upper_limit(self, chiller):
        series = make_series([3.0 + 0.1 * i for i in range(36)])["cold_temp"]
        result = forecast_trend(series, chiller.max_safe_c, min_safe_c=chiller.min_safe_c)
        assert result["status"] == "at_risk"
        assert result["direction"] == "warm"

    def test_cooling_trend_warns_of_freezing(self, chiller):
        series = make_series([3.5 - 0.1 * i for i in range(36)])["cold_temp"]
        result = forecast_trend(series, chiller.max_safe_c, min_safe_c=chiller.min_safe_c)
        assert result["status"] == "at_risk"
        assert result["direction"] == "cold"

    def test_without_a_lower_bound_cooling_is_not_a_risk(self, freezer):
        series = make_series([-17.0 - 0.1 * i for i in range(36)])["cold_temp"]
        assert forecast_trend(series, freezer.max_safe_c, min_safe_c=None)["status"] == "stable"

    def test_flat_series_is_stable(self, chiller):
        series = make_series([3.0] * 36)["cold_temp"]
        result = forecast_trend(series, chiller.max_safe_c, min_safe_c=chiller.min_safe_c)
        assert result["status"] == "stable"
        assert result["direction"] is None


class TestSilenceEvents:
    def _raw(self, minutes):
        ts = pd.Timestamp("2017-08-01T00:00:00Z") + pd.to_timedelta(minutes, unit="m")
        return pd.DataFrame({"ts": ts, "temp": 4.0})

    def test_a_long_gap_becomes_a_critical_event_with_the_last_known_temperature(self):
        raw = self._raw([0, 5, 10, 100, 105])  # silent from minute 10 to 100
        events = detect_silences(raw, raw["ts"].iloc[-1], threshold_min=30)
        assert len(events) == 1
        assert events[0].kind == "sensor_silent" and events[0].minutes == 90
        assert events[0].severity == "critical"

    def test_a_sensor_that_dies_before_its_premises_stops_is_caught(self):
        raw = self._raw([0, 5, 10])
        until = pd.Timestamp("2017-08-01T02:00:00Z")  # its sibling channels kept reporting
        events = detect_silences(raw, until, threshold_min=30)
        assert len(events) == 1 and events[0].minutes == 110

    def test_normal_five_minute_reporting_raises_nothing(self):
        raw = self._raw(list(range(0, 300, 5)))
        assert detect_silences(raw, raw["ts"].iloc[-1], threshold_min=30) == []


def test_trend_forecast_judges_the_present_from_the_latest_reading(chiller):
    """A fitted line still pulled up by earlier warm readings must not make a unit that is
    back inside its band read as "already above the safe limit"."""
    series = make_series([7.4] * 15 + [4.2])["cold_temp"]
    result = forecast_trend(series, chiller.max_safe_c, min_safe_c=chiller.min_safe_c)
    assert result["current_c"] == 4.2
    assert result["minutes_to_limit"] != 0
