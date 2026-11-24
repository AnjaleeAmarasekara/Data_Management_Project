import numpy as np
import pandas as pd

from dq_engine.checks.drift import check_drift
from dq_engine.checks.missing import check_missing
from dq_engine.checks.noise import check_noise, check_timestamp_integrity
from dq_engine.checks.outlier import check_outliers
from dq_engine.faults import (
    drop_rows,
    inject_calibration_bias,
    inject_drift,
    inject_duplicate_timestamp,
    inject_noise,
    inject_spike,
)
from dq_engine.scoring import score_window

from dq_engine.config import WINDOW_MINUTES

from .conftest import READINGS_PER_WINDOW, SAMPLING_INTERVAL_S


class TestMissing:
    def test_clean_window_not_flagged(self, clean_window):
        result = check_missing(clean_window, SAMPLING_INTERVAL_S)
        assert result.flag is False

    def test_dropout_is_flagged(self, clean_window):
        faulty = drop_rows(clean_window, start=8, count=8)
        result = check_missing(faulty, SAMPLING_INTERVAL_S)
        assert result.flag is True
        assert result.evidence["gap_count"] == 1
        assert result.evidence["estimated_missing_readings"] > 0


class TestOutlier:
    def test_clean_window_not_flagged(self, clean_window):
        result = check_outliers(clean_window)
        assert result.flag is False

    def test_spike_is_flagged(self, clean_window):
        faulty = inject_spike(clean_window, at=12, magnitude=15.0)
        result = check_outliers(faulty)
        assert result.flag is True
        assert result.evidence["statistical_outlier_count"] >= 1

    def test_extreme_calibration_bias_is_flagged_via_plausible_range(self, clean_window):
        faulty = inject_calibration_bias(clean_window, offset=60.0)  # ~64C, physically implausible
        result = check_outliers(faulty)
        assert result.flag is True
        assert result.evidence["implausible_count"] > 0

    def test_moderate_calibration_bias_is_a_known_blind_spot(self, clean_window):
        """A constant offset that stays within the plausible range shifts the whole
        window uniformly, so window-local statistics (IQR, range) can't distinguish it
        from a correctly-calibrated sensor reading a genuinely warmer unit. This is a
        real limitation of single-sensor, window-based checks -- catching it needs
        cross-referencing against another sensor or a periodic ground-truth calibration
        check, which is out of scope for this phase. Documented in docs/data_linkage.md
        adjacent design notes rather than silently assumed solved.
        """
        faulty = inject_calibration_bias(clean_window, offset=10.0)  # ~14C, still "plausible"
        result = check_outliers(faulty)
        assert result.flag is False


class TestDrift:
    def test_clean_window_not_flagged(self, clean_window):
        result = check_drift(clean_window)
        assert result.flag is False

    def test_gradual_drift_is_flagged(self, clean_window):
        faulty = inject_drift(clean_window, slope_per_reading=0.4)
        result = check_drift(faulty)
        assert result.flag is True


class TestNoise:
    def test_clean_window_not_flagged(self, clean_window):
        result = check_noise(clean_window)
        assert result.flag is False

    def test_interference_is_flagged(self, clean_window):
        faulty = inject_noise(clean_window, magnitude=5.0, fraction=0.3)
        result = check_noise(faulty)
        assert result.flag is True
        assert result.evidence["noisy_transitions"] > 0

    def test_clean_timestamps_not_flagged(self, clean_window):
        result = check_timestamp_integrity(clean_window)
        assert result.flag is False

    def test_duplicate_timestamp_is_flagged(self, clean_window):
        faulty = inject_duplicate_timestamp(clean_window, at=12)
        result = check_timestamp_integrity(faulty)
        assert result.flag is True
        assert result.evidence["duplicate_count"] == 1


class TestScoring:
    def test_clean_window_scores_high(self, clean_window):
        score = score_window("test-device", clean_window, SAMPLING_INTERVAL_S)
        assert score.quality_label == "High"
        assert score.trust_value >= 90
        assert not any([score.missing_flag, score.outlier_flag, score.drift_flag, score.noise_flag])

    def test_multi_fault_window_scores_low(self, clean_window):
        faulty = drop_rows(clean_window, start=4, count=6)
        faulty = inject_spike(faulty, at=10, magnitude=20.0)
        faulty = inject_drift(faulty, slope_per_reading=0.5)
        score = score_window("test-device", faulty, SAMPLING_INTERVAL_S)
        assert score.quality_label in ("Low", "Medium")
        assert score.missing_flag and score.outlier_flag and score.drift_flag
        assert score.trust_value < 90


class TestSeverityFloors:
    def test_single_late_reading_is_not_missing_data(self, clean_window):
        faulty = drop_rows(clean_window, start=10, count=1)  # one late reading in a 24-sample window
        assert check_missing(faulty, SAMPLING_INTERVAL_S).flag is False

    def test_isolated_spike_is_an_outlier_not_noise(self, clean_window):
        faulty = inject_spike(clean_window, at=12, magnitude=15.0)
        assert check_outliers(faulty).flag is True
        assert check_noise(faulty).flag is False


class TestScoringRules:
    def test_confirmed_drift_can_never_be_high(self, clean_window):
        score = score_window("d", inject_drift(clean_window, slope_per_reading=0.4), SAMPLING_INTERVAL_S)
        assert score.drift_flag and score.trust_value <= 80 and score.quality_label != "High"

    def test_physically_impossible_window_is_low(self, clean_window):
        score = score_window("d", inject_calibration_bias(clean_window, offset=60.0), SAMPLING_INTERVAL_S)
        assert score.quality_label == "Low"

    def test_flagged_readings_are_reported_per_reason(self, clean_window):
        score = score_window("d", inject_spike(clean_window, at=12, magnitude=15.0), SAMPLING_INTERVAL_S)
        assert clean_window["ts"].iloc[12] in score.flagged["outlier"]


class TestIntegrity:
    def test_clean_window_not_flagged(self, clean_window):
        from dq_engine.checks.integrity import check_impossible_jump, check_replay

        assert check_replay(clean_window).flag is False
        assert check_impossible_jump(clean_window).flag is False

    def test_replayed_run_is_flagged(self, clean_window):
        from dq_engine.checks.integrity import check_replay
        from dq_engine.faults import inject_replay

        faulty = inject_replay(clean_window, start=8, count=8)
        result = check_replay(faulty)
        assert result.flag is True
        assert result.evidence["longest_run"] >= 8

    def test_impossible_jump_is_flagged(self, clean_window):
        from dq_engine.checks.integrity import check_impossible_jump
        from dq_engine.faults import inject_impossible_jump

        faulty = inject_impossible_jump(clean_window, at=12, magnitude=20.0)
        result = check_impossible_jump(faulty)
        assert result.flag is True
        # An isolated spike this large breaks physical plausibility twice: jumping up
        # into the spike and back down out of it.
        assert result.evidence["impossible_count"] == 2

    def test_statistical_outlier_is_not_an_impossible_jump(self, clean_window):
        """A spike well within physical limits (e.g. a door left open) is the outlier
        check's job -- it should not also trip the physical-impossibility check."""
        from dq_engine.checks.integrity import check_impossible_jump
        from dq_engine.faults import inject_spike

        faulty = inject_spike(clean_window, at=12, magnitude=8.0)
        assert check_impossible_jump(faulty).flag is False


class TestScoringIntegrity:
    def test_impossible_jump_forces_low(self, clean_window):
        from dq_engine.faults import inject_impossible_jump

        score = score_window("d", inject_impossible_jump(clean_window, at=12, magnitude=25.0), SAMPLING_INTERVAL_S)
        assert score.integrity_flag is True
        assert score.quality_label == "Low"

    def test_replay_is_scored_where_the_zone_supports_it(self, clean_window):
        """Replay detection was disabled outright against the earlier development dataset,
        whose sensor repeated ~83% of readings naturally. These loggers repeat 7.6-27%
        depending on zone, so the check is now part of the integrity component -- a frozen
        run caps trust rather than being ignored."""
        from dq_engine.faults import inject_replay

        replayed = score_window("BKK-012-MS", inject_replay(clean_window, start=5, count=14), SAMPLING_INTERVAL_S)
        assert replayed.integrity_flag is True
        assert replayed.trust_value <= 50.0
        assert replayed.quality_label != "High"

    def test_replay_is_disabled_for_the_vegetable_drawer(self, clean_window):
        """A vegetable drawer is thermally damped and genuinely sits still: 48.6% of its
        readings repeat, and 23.7% of clean drawer windows would be flagged at these
        thresholds. It is a declared blind spot rather than a false-alarm generator, so a
        replayed drawer window must score exactly as if untouched."""
        from dq_engine.faults import inject_replay

        baseline = score_window("BKK-012-VD", clean_window, SAMPLING_INTERVAL_S)
        replayed = score_window("BKK-012-VD", inject_replay(clean_window, start=5, count=14), SAMPLING_INTERVAL_S)
        assert replayed.integrity_flag is False
        assert replayed.trust_value == baseline.trust_value


class TestHumidityIsValidated:
    """Humidity feeds the anomaly detector's features, so leaving it unchecked would break
    "quality before analytics" for one of the two measured quantities. These tests exist
    because it was unchecked for a while and nothing caught it."""

    def test_corrupt_humidity_is_flagged_even_when_temperature_is_clean(self, clean_window):
        faulty = clean_window.copy()
        faulty.loc[12, "humidity"] = 180.0  # impossible: humidity is a percentage
        score = score_window("BKK-012-MS", faulty, SAMPLING_INTERVAL_S)
        assert score.outlier_flag is True
        assert score.trust_value < 100

    def test_humidity_noise_is_flagged(self, clean_window):
        faulty = clean_window.copy()
        faulty.loc[::2, "humidity"] = faulty.loc[::2, "humidity"] + 40.0  # violent oscillation
        score = score_window("BKK-012-MS", faulty, SAMPLING_INTERVAL_S)
        assert score.noise_flag is True

    def test_a_channel_without_humidity_still_scores(self, clean_window):
        """The product probe is a gel with a temperature sensor and no hygrometer."""
        probe = clean_window.copy()
        probe["humidity"] = None
        score = score_window("BKK-012-PT", probe, SAMPLING_INTERVAL_S)
        assert score.quality_label == "High"
        assert score.outlier_flag is False


class TestSilenceIsNeverInvisible:
    """An outage used to vanish from the quality record: windows were built from the readings
    that arrived, so a window in which the sensor sent nothing produced no row, and silence at
    the edge of a window left no gap to measure. On real data a 3-hour and a 6-hour outage
    produced no missing-data flag at all."""

    def _bounds(self, w):
        return w["ts"].iloc[0], w["ts"].iloc[0] + pd.Timedelta(minutes=WINDOW_MINUTES)

    def test_silence_at_the_end_of_a_window_is_caught(self, clean_window):
        start, end = self._bounds(clean_window)
        quiet_after_40_min = clean_window[clean_window["ts"] < start + pd.Timedelta(minutes=40)]
        result = check_missing(quiet_after_40_min, SAMPLING_INTERVAL_S, window_start=start, window_end=end)
        assert result.flag is True
        assert result.evidence["missing_ratio"] > 0.5

    def test_silence_at_the_start_of_a_window_is_caught(self, clean_window):
        start, end = self._bounds(clean_window)
        quiet_until_80_min = clean_window[clean_window["ts"] >= start + pd.Timedelta(minutes=80)]
        assert check_missing(quiet_until_80_min, SAMPLING_INTERVAL_S, window_start=start, window_end=end).flag

    def test_an_empty_window_is_scored_as_untrustworthy_not_skipped(self, clean_window):
        start, end = self._bounds(clean_window)
        score = score_window("d", clean_window.iloc[0:0], SAMPLING_INTERVAL_S, window_start=start, window_end=end)
        assert score.missing_flag is True
        assert score.trust_value == 0.0 and score.quality_label == "Low"

    def test_trust_cannot_exceed_the_share_of_the_window_observed(self, clean_window):
        start, end = self._bounds(clean_window)
        half = clean_window.iloc[: len(clean_window) // 2]
        score = score_window("d", half, SAMPLING_INTERVAL_S, window_start=start, window_end=end)
        assert score.trust_value <= 55.0  # about half the window was seen
        assert score.quality_label == "Low"

    def test_a_complete_window_is_unaffected_by_the_bounds(self, clean_window):
        start, end = self._bounds(clean_window)
        score = score_window("d", clean_window, SAMPLING_INTERVAL_S, window_start=start, window_end=end)
        assert score.missing_flag is False and score.quality_label == "High"

    def test_a_multi_window_outage_produces_low_windows_on_the_grid(self, clean_window):
        from dq_engine.run_scoring import window_grid

        # six windows of readings, with the middle two silent
        ts = pd.date_range("2024-01-01T00:00:00Z", periods=6 * READINGS_PER_WINDOW, freq=f"{int(SAMPLING_INTERVAL_S)}s")
        rng = np.random.default_rng(seed=7)
        # realistic small noise: a perfectly flat series would rightly be flagged as a
        # frozen (replayed) signal, which is not what this test is about
        full = pd.DataFrame({"ts": ts, "temp": 4.0 + rng.normal(0, 0.15, len(ts)),
                             "humidity": 55.0 + rng.normal(0, 1.0, len(ts))})
        gap_start, gap_end = ts[2 * READINGS_PER_WINDOW], ts[4 * READINGS_PER_WINDOW]
        with_outage = full[(full["ts"] < gap_start) | (full["ts"] >= gap_end)]
        grid = window_grid(ts[0], ts[-1])
        labels = []
        for i, (a, b) in enumerate(grid):
            m = (with_outage["ts"] >= a) & ((with_outage["ts"] <= b) if i == len(grid) - 1 else (with_outage["ts"] < b))
            labels.append(score_window("d", with_outage[m].reset_index(drop=True), SAMPLING_INTERVAL_S, a, b).quality_label)
        assert labels.count("Low") == 2 and len(labels) == 6
