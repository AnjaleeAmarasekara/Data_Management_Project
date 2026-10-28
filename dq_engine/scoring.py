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
from dq_engine.checks.integrity import check_impossible_jump
from dq_engine.checks.missing import check_missing
from dq_engine.checks.noise import check_noise, check_timestamp_integrity
from dq_engine.checks.outlier import check_outliers

# check_replay (checks/integrity.py) is deliberately NOT part of the composite score.
# Validated against the real dataset, it produces unusable false-positive rates: this
# sensor hardware caches a reading between its own internal sampling cycles, so exact
# consecutive-value repeats cover a *median* of ~83% of readings naturally, with the
# longest natural same-value run reaching 353 of ~350 readings in one window -- there is
# no threshold that separates "natural" from "injected" for this specific hardware. The
# check itself is correct (tested against synthetic data, where this confound doesn't
# exist) and kept for future work with different sensor hardware or cross-sensor
# validation; see docs/intrusion_detection.md for the full investigation.

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


def label_for(trust_value: float) -> str:
    high, medium = LABEL_THRESHOLDS
    if trust_value >= high:
        return "High"
    if trust_value >= medium:
        return "Medium"
    return "Low"


def score_window(device_id: str, window: pd.DataFrame, expected_interval_s: float = 5.0) -> QualityScore:
    """window: one device's readings sorted by ts, columns include ts/temp/humidity."""
    results = {
        "missing": check_missing(window, expected_interval_s),
        "outlier": check_outliers(window, "temp"),
        "drift": check_drift(window, "temp"),
        "noise": check_noise(window, "temp"),
        "timestamp": check_timestamp_integrity(window),
        "impossible_jump": check_impossible_jump(window, "temp"),
    }
    integrity_penalty = results["impossible_jump"].penalty
    integrity_flag = results["impossible_jump"].flag

    weighted_penalty = (
        sum(WEIGHTS[name] * results[name].penalty for name in ("missing", "outlier", "drift", "noise", "timestamp"))
        + WEIGHTS["integrity"] * integrity_penalty
    )
    trust_value = round(max(0.0, 100.0 - weighted_penalty * 100), 2)

    implausible_fraction = results["outlier"].evidence.get("implausible_count", 0) / max(len(window), 1)
    if implausible_fraction >= IMPLAUSIBLE_CAP_FRACTION:
        trust_value = min(trust_value, IMPLAUSIBLE_CAP_TRUST)
    if results["drift"].flag:
        trust_value = min(trust_value, DRIFT_CAP_TRUST)
    if results["impossible_jump"].flag:
        trust_value = min(trust_value, IMPOSSIBLE_JUMP_CAP_TRUST)

    return QualityScore(
        device_id=device_id,
        window_start=window["ts"].min(),
        window_end=window["ts"].max(),
        missing_flag=results["missing"].flag,
        outlier_flag=results["outlier"].flag,
        drift_flag=results["drift"].flag,
        noise_flag=results["noise"].flag or results["timestamp"].flag,
        integrity_flag=integrity_flag,
        trust_value=trust_value,
        quality_label=label_for(trust_value),
        evidence={name: r.evidence for name, r in results.items()},
        flagged={
            "gap": results["missing"].flagged_timestamps,
            "outlier": results["outlier"].flagged_timestamps,
            "noise": results["noise"].flagged_timestamps,
            "integrity": results["impossible_jump"].flagged_timestamps,
        },
    )
