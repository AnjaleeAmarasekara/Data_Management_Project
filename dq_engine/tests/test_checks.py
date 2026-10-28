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

from .conftest import SAMPLING_INTERVAL_S


class TestMissing:
    def test_clean_window_not_flagged(self, clean_window):
        result = check_missing(clean_window, SAMPLING_INTERVAL_S)
        assert result.flag is False

    def test_dropout_is_flagged(self, clean_window):
        faulty = drop_rows(clean_window, start=100, count=20)
        result = check_missing(faulty, SAMPLING_INTERVAL_S)
        assert result.flag is True
        assert result.evidence["gap_count"] == 1
        assert result.evidence["estimated_missing_readings"] > 0


class TestOutlier:
    def test_clean_window_not_flagged(self, clean_window):
        result = check_outliers(clean_window)
        assert result.flag is False

    def test_spike_is_flagged(self, clean_window):
        faulty = inject_spike(clean_window, at=150, magnitude=15.0)
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
        faulty = inject_drift(clean_window, slope_per_reading=0.02)
        result = check_drift(faulty)
        assert result.flag is True


class TestNoise:
    def test_clean_window_not_flagged(self, clean_window):
        result = check_noise(clean_window)
        assert result.flag is False

    def test_interference_is_flagged(self, clean_window):
        faulty = inject_noise(clean_window, magnitude=3.0, fraction=0.3)
        result = check_noise(faulty)
        assert result.flag is True
        assert result.evidence["noisy_transitions"] > 0

    def test_clean_timestamps_not_flagged(self, clean_window):
        result = check_timestamp_integrity(clean_window)
        assert result.flag is False

    def test_duplicate_timestamp_is_flagged(self, clean_window):
        faulty = inject_duplicate_timestamp(clean_window, at=200)
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
        faulty = drop_rows(clean_window, start=50, count=15)
        faulty = inject_spike(faulty, at=100, magnitude=20.0)
        faulty = inject_drift(faulty, slope_per_reading=0.03)
        score = score_window("test-device", faulty, SAMPLING_INTERVAL_S)
        assert score.quality_label in ("Low", "Medium")
        assert score.missing_flag and score.outlier_flag and score.drift_flag
        assert score.trust_value < 90


class TestSeverityFloors:
    def test_single_late_reading_is_not_missing_data(self, clean_window):
        faulty = drop_rows(clean_window, start=100, count=3)  # ~15 s hiccup among 360 readings
        assert check_missing(faulty, SAMPLING_INTERVAL_S).flag is False

    def test_isolated_spike_is_an_outlier_not_noise(self, clean_window):
        faulty = inject_spike(clean_window, at=150, magnitude=15.0)
        assert check_outliers(faulty).flag is True
        assert check_noise(faulty).flag is False


class TestScoringRules:
    def test_confirmed_drift_can_never_be_high(self, clean_window):
        score = score_window("d", inject_drift(clean_window, slope_per_reading=0.02), SAMPLING_INTERVAL_S)
        assert score.drift_flag and score.trust_value <= 80 and score.quality_label != "High"

    def test_physically_impossible_window_is_low(self, clean_window):
        score = score_window("d", inject_calibration_bias(clean_window, offset=60.0), SAMPLING_INTERVAL_S)
        assert score.quality_label == "Low"

    def test_flagged_readings_are_reported_per_reason(self, clean_window):
        score = score_window("d", inject_spike(clean_window, at=150, magnitude=15.0), SAMPLING_INTERVAL_S)
        assert clean_window["ts"].iloc[150] in score.flagged["outlier"]


class TestIntegrity:
    def test_clean_window_not_flagged(self, clean_window):
        from dq_engine.checks.integrity import check_impossible_jump, check_replay

        assert check_replay(clean_window).flag is False
        assert check_impossible_jump(clean_window).flag is False

    def test_replayed_run_is_flagged(self, clean_window):
        from dq_engine.checks.integrity import check_replay
        from dq_engine.faults import inject_replay

        faulty = inject_replay(clean_window, start=100, count=40)
        result = check_replay(faulty)
        assert result.flag is True
        assert result.evidence["longest_run"] >= 40

    def test_impossible_jump_is_flagged(self, clean_window):
        from dq_engine.checks.integrity import check_impossible_jump
        from dq_engine.faults import inject_impossible_jump

        faulty = inject_impossible_jump(clean_window, at=150, magnitude=20.0)
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

        faulty = inject_spike(clean_window, at=150, magnitude=8.0)
        assert check_impossible_jump(faulty).flag is False


class TestScoringIntegrity:
    def test_impossible_jump_forces_low(self, clean_window):
        from dq_engine.faults import inject_impossible_jump

        score = score_window("d", inject_impossible_jump(clean_window, at=150, magnitude=25.0), SAMPLING_INTERVAL_S)
        assert score.integrity_flag is True
        assert score.quality_label == "Low"

    def test_replay_is_not_part_of_composite_score(self, clean_window):
        """check_replay is validated in isolation (TestIntegrity above) but deliberately
        excluded from score_window: against the real dataset it produces unusable
        false-positive rates (median ~83% of readings are exact consecutive repeats,
        a hardware sampling-cache artifact, not a tampering signal). A replay-only
        window must therefore score exactly as if untouched."""
        from dq_engine.faults import inject_replay

        baseline = score_window("d", clean_window, SAMPLING_INTERVAL_S)
        replayed = score_window("d", inject_replay(clean_window, start=50, count=50), SAMPLING_INTERVAL_S)
        assert replayed.integrity_flag is False
        assert replayed.trust_value == baseline.trust_value
