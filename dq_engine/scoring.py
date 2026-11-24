"""Composite data quality scoring.

Combines the individual check results into a single trust_value (0-100) and
quality_label, matching the proposal's class diagram (SensorRecord -> DataQualityCheck
-> QualityScore). Weights reflect how directly each fault type undermines trust: missing
data, outliers, drift and integrity faults are weighted heaviest, since decisions could be
made on data that isn't there, is flatly wrong, is systematically biased, or was fabricated
rather than measured. Isolated faults (one spike, one duplicate) barely move the window
score because the specific reading is excluded downstream (row-level flags); window-level
faults (drift, heavy dropout, heavy noise, a tampering signature) cost real trust.
"""

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from dq_engine.checks.drift import check_drift
from dq_engine.checks.integrity import check_impossible_jump, check_replay
from dq_engine.checks.missing import check_missing
from dq_engine.checks.noise import check_noise, check_timestamp_integrity
from dq_engine.checks.outlier import check_outliers
from dq_engine.checks.result import CheckResult
from dq_engine.config import (
    HUMIDITY_NOISE_RATE,
    REPLAY_MIN_RATIO,
    REPLAY_MIN_RUN,
    drift_thresholds,
    noise_rate_threshold,
    replay_enabled,
)

# check_replay and check_impossible_jump both answer "was this fabricated rather than
# measured?", so they share the integrity weight rather than each getting their own. They
# differ in how conclusive they are: a physically impossible jump is proof the reading did
# not come from the real world, while a frozen run is strong suspicion, so they carry
# different caps.
#
# Replay detection is enabled per zone. It was disabled entirely against the earlier
# development dataset, whose sensor repeated ~83% of readings naturally; these loggers
# repeat 7.6-27% depending on zone, which makes the check usable everywhere except the
# vegetable drawer. See dq_engine/config.py and docs/intrusion_detection.md.

WEIGHTS = {
    "missing": 0.25,
    "outlier": 0.25,
    "drift": 0.20,
    "noise": 0.08,
    "timestamp": 0.04,
    "integrity": 0.18,
}

# If at least this fraction of a window is physically impossible, no other evidence matters.
IMPLAUSIBLE_CAP_FRACTION = 0.5
IMPLAUSIBLE_CAP_TRUST = 30.0

# Confirmed drift biases every reading in the window, so such a window can never be "High".
DRIFT_CAP_TRUST = 80.0

# A physically impossible single-step jump is the strongest tampering signal this system
# can see in one sensor's data alone -- treat it like a confirmed integrity failure, not
# a statistical nuisance.
IMPOSSIBLE_JUMP_CAP_TRUST = 20.0

# A frozen run is strong suspicion rather than proof -- a genuinely steady unit can
# produce one -- so it caps trust lower than "High" without forcing the window to Low.
REPLAY_CAP_TRUST = 50.0

LABEL_THRESHOLDS = (85.0, 60.0)  # >=85 High, >=60 Medium, else Low


@dataclass
class QualityScore:
    device_id: str
    window_start: datetime
    window_end: datetime
    missing_flag: bool
    outlier_flag: bool
    drift_flag: bool
    noise_flag: bool
    integrity_flag: bool
    trust_value: float
    quality_label: str
    evidence: dict
    flagged: dict  # reason ('gap' | 'outlier' | 'noise' | 'integrity') -> list of reading timestamps


def _worse(temp_result: CheckResult, humidity_result: CheckResult) -> CheckResult:
    """Combine the same check run over temperature and humidity.

    The window is only as trustworthy as its worst channel, so the larger penalty wins and
    the flags are OR-ed. Both channels' evidence is kept, and the flagged timestamps are
    merged so a bad humidity reading is excluded from analytics exactly as a bad
    temperature reading would be.
    """
    return CheckResult(
        flag=temp_result.flag or humidity_result.flag,
        penalty=max(temp_result.penalty, humidity_result.penalty),
        evidence={"temp": temp_result.evidence, "humidity": humidity_result.evidence},
        flagged_timestamps=sorted(set(temp_result.flagged_timestamps) | set(humidity_result.flagged_timestamps)),
    )


def label_for(trust_value: float) -> str:
    high, medium = LABEL_THRESHOLDS
    if trust_value >= high:
        return "High"
    if trust_value >= medium:
        return "Medium"
    return "Low"


def _score_empty_window(device_id: str, window_start, window_end) -> QualityScore:
    """A window in which the sensor sent nothing at all. It used to produce no row, so a
    six-hour outage left no trace in the quality record; now it is scored as fully missing,
    with zero trust, because there is no evidence to trust."""
    if window_start is None or window_end is None:
        raise ValueError("an empty window can only be scored when its bounds are known")
    return QualityScore(
        device_id=device_id,
        window_start=window_start,
        window_end=window_end,
        missing_flag=True,
        outlier_flag=False,
        drift_flag=False,
        noise_flag=False,
        integrity_flag=False,
        trust_value=0.0,
        quality_label=label_for(0.0),
        evidence={"missing": {"reason": "no readings in window", "missing_ratio": 1.0}},
        flagged={"gap": [], "outlier": [], "noise": [], "integrity": []},
    )


def score_window(
    device_id: str,
    window: pd.DataFrame,
    expected_interval_s: float = 5.0,
    window_start: pd.Timestamp | None = None,
    window_end: pd.Timestamp | None = None,
) -> QualityScore:
    """window: one device's readings sorted by ts, columns include ts/temp/humidity.

    window_start / window_end are the period the window was meant to cover. Pass them
    whenever they are known: they are what lets the missing-data check see a sensor that
    went quiet at the edge of a window, and they are recorded as the window's bounds
    instead of the first and last reading that happened to arrive."""
    if window.empty:
        return _score_empty_window(device_id, window_start, window_end)
    # Humidity is checked as well as temperature. It is not decoration: the anomaly
    # detector takes humidity as a feature, so an unchecked humidity reading would flow
    # straight into analytics and break the project's own "quality before analytics" rule
    # for one of its two measured quantities. The outlier and noise components therefore
    # take the worse of the two channels, and the evidence records which one fired.
    temp_outlier = check_outliers(window, "temp")
    temp_noise = check_noise(window, "temp", rate_threshold=noise_rate_threshold(device_id))
    hum_outlier = check_outliers(window, "humidity")
    hum_noise = check_noise(window, "humidity", rate_threshold=HUMIDITY_NOISE_RATE)

    results = {
        "missing": check_missing(window, expected_interval_s, window_start=window_start, window_end=window_end),
        "outlier": _worse(temp_outlier, hum_outlier),
        "drift": check_drift(window, "temp", *drift_thresholds(device_id)),
        # the noise threshold depends on the equipment the sensor sits in, so it is looked
        # up per device rather than fixed for every unit (see dq_engine/config.py)
        "noise": _worse(temp_noise, hum_noise),
        "timestamp": check_timestamp_integrity(window),
        "impossible_jump": check_impossible_jump(window, "temp"),
        "replay": (
            check_replay(window, "temp", min_run=REPLAY_MIN_RUN, min_ratio=REPLAY_MIN_RATIO)
            if replay_enabled(device_id)
            else CheckResult(flag=False, penalty=0.0, evidence={"reason": "disabled_for_zone"})
        ),
    }
    integrity_penalty = max(results["impossible_jump"].penalty, results["replay"].penalty)
    integrity_flag = results["impossible_jump"].flag or results["replay"].flag

    weighted_penalty = (
        sum(WEIGHTS[name] * results[name].penalty for name in ("missing", "outlier", "drift", "noise", "timestamp"))
        + WEIGHTS["integrity"] * integrity_penalty
    )
    trust_value = round(max(0.0, 100.0 - weighted_penalty * 100), 2)

    # The outlier evidence is now nested per channel, so read the count from whichever
    # channel reported the most physically impossible values rather than from the top
    # level, where it no longer exists.
    implausible_count = max(
        (ch.get("implausible_count", 0) for ch in results["outlier"].evidence.values() if isinstance(ch, dict)),
        default=0,
    )
    implausible_fraction = implausible_count / max(len(window), 1)
    if implausible_fraction >= IMPLAUSIBLE_CAP_FRACTION:
        trust_value = min(trust_value, IMPLAUSIBLE_CAP_TRUST)
    # You cannot trust more of a window than you actually observed. The missing component
    # carries a quarter of the weight, so without this a window that was 90% silent would
    # still score about 77 and read as Medium.
    missing_ratio = results["missing"].evidence.get("missing_ratio", 0.0)
    if results["missing"].flag and missing_ratio:
        trust_value = min(trust_value, round(100.0 * (1.0 - missing_ratio), 2))
    if results["drift"].flag:
        trust_value = min(trust_value, DRIFT_CAP_TRUST)
    if results["replay"].flag:
        trust_value = min(trust_value, REPLAY_CAP_TRUST)
    if results["impossible_jump"].flag:
        trust_value = min(trust_value, IMPOSSIBLE_JUMP_CAP_TRUST)

    return QualityScore(
        device_id=device_id,
        window_start=window_start if window_start is not None else window["ts"].min(),
        window_end=window_end if window_end is not None else window["ts"].max(),
        missing_flag=results["missing"].flag,
        outlier_flag=results["outlier"].flag,
        drift_flag=results["drift"].flag,
        noise_flag=results["noise"].flag or results["timestamp"].flag,
        integrity_flag=integrity_flag,
        trust_value=trust_value,
        quality_label=label_for(trust_value),
        evidence={name: r.evidence for name, r in results.items()},
        # Row-level flags are the readings analytics discards, so only conclusive evidence
        # belongs here. An impossible jump proves the reading was not measured, so its rows
        # go. A frozen run is suspicion, not proof -- a genuinely steady unit can produce one
        # -- so replay caps the window's trust and shows as an integrity flag, but its rows
        # stay in the series rather than deleting an hour of history on suspicion alone.
        flagged={
            "gap": results["missing"].flagged_timestamps,
            "outlier": results["outlier"].flagged_timestamps,
            "noise": results["noise"].flagged_timestamps,
            "integrity": results["impossible_jump"].flagged_timestamps,
        },
    )
