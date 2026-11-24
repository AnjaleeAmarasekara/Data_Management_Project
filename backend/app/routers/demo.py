"""Fault-injection demo for the review.

Takes a known-clean real window, corrupts an in-memory copy with a chosen synthetic
fault (the same helpers the test suite uses), and runs the real quality layer on both
versions. Nothing is written to the database.
"""

from typing import Literal

import numpy as np
import pandas as pd
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from analytics.config import UNITS
from backend.app.audit import log_action
from backend.app.auth import User, get_current_user
from backend.app.db import connect
from dq_engine.config import DEFAULT_INTERVAL_S, EXPECTED_INTERVAL_S, drift_thresholds, noise_rate_threshold
from dq_engine.faults import (
    drop_rows,
    inject_calibration_bias,
    inject_drift,
    inject_duplicate_timestamp,
    inject_impossible_jump,
    inject_noise,
    inject_replay,
    inject_spike,
)
from dq_engine.scoring import QualityScore, score_window

router = APIRouter(tags=["demo"])

FaultName = Literal[
    "dropout", "spike", "drift", "noise", "duplicate_timestamp",
    "calibration_bias_subtle", "calibration_bias_extreme",
    "replay_attack", "data_injection", "sensor_silent",
]

# Each fault returns (description, corrupted copy). Magnitudes are derived from the
# selected unit's own thresholds rather than fixed, because the same fault means different
# things in different equipment: a freezer running a defrost cycle legitimately shifts
# several degrees within a window, so a drift small enough to alarm a vegetable drawer is
# normal behaviour there. Deriving the magnitude keeps every demo an honest test of the
# check -- the injected fault is always outside what that unit does naturally.
# Margin above the detection threshold, so a demo never lands marginally under it.
DRIFT_DEMO_MARGIN = 1.4


def _drift_fault(df, device_id):
    """The ramp is sized against this window's own trend, not fixed.

    A fixed multiple of the threshold is not enough: a window that is already drifting
    gently downwards cancels part of the injected rise. On one real unit a 2.5x ramp
    landed at a shift of 3.17 against a 3.5 threshold, so the demo reported "not detected"
    for a fault that was genuinely injected -- which reads as a broken check rather than
    the honest limitation it is not. Measuring the window first and adding enough to clear
    both drift tests with margin keeps every run a true test of the check.
    """
    slope_threshold, shift_threshold = drift_thresholds(device_id)
    series = df["temp"].dropna()
    n = len(series)
    half = n // 2
    natural_shift = float(series.iloc[half:].mean() - series.iloc[:half].mean())
    natural_slope = float(np.polyfit(np.arange(n), series.values, 1)[0]) if n > 1 else 0.0

    # A linear ramp of total rise R shifts the half-window means by about R/2 and adds
    # R/(n-1) to the slope; clear whichever test needs the larger ramp.
    need_for_shift = 2.0 * (shift_threshold * DRIFT_DEMO_MARGIN - natural_shift)
    need_for_slope = (slope_threshold * DRIFT_DEMO_MARGIN - natural_slope) * max(n - 1, 1)
    rise = max(need_for_shift, need_for_slope, shift_threshold)

    return (
        f"Sensor drift: reading creeps up ~{rise:.0f} C across the window",
        inject_drift(df, rise / max(n - 1, 1)),
    )


def _noise_fault(df, device_id):
    magnitude = 2.0 * noise_rate_threshold(device_id)
    return (
        f"Electrical noise: 30% of readings jump by +/-{magnitude:.0f} C",
        inject_noise(df, magnitude, 0.3),
    )


FAULTS = {
    "dropout": lambda df, _: (
        f"Communication dropout: about {len(df) // 3 * 5} minutes of readings lost",
        drop_rows(df, len(df) // 3, len(df) // 3),
    ),
    "spike": lambda df, _: ("Interference spike: one reading jumps by +8 C", inject_spike(df, len(df) // 2, 8.0)),
    "drift": _drift_fault,
    "noise": _noise_fault,
    "duplicate_timestamp": lambda df, _: (
        "Network retransmission: a duplicated timestamp",
        inject_duplicate_timestamp(df, len(df) // 2),
    ),
    "calibration_bias_subtle": lambda df, _: (
        "Calibration error: every reading offset by +6 C (still physically plausible)",
        inject_calibration_bias(df, 6.0),
    ),
    "calibration_bias_extreme": lambda df, _: (
        "Gross calibration error: every reading offset by +80 C",
        inject_calibration_bias(df, 80.0),
    ),
    "replay_attack": lambda df, _: (
        "Replay/spoofing attack: a block of readings frozen to one exact value",
        inject_replay(df, len(df) // 4, max(14, len(df) // 2)),
    ),
    "data_injection": lambda df, _: (
        "Data injection attack: one reading jumps further than physically possible",
        inject_impossible_jump(df, len(df) // 2, 25.0),
    ),
    "sensor_silent": lambda df, _: (
        "Sensor goes silent: it stops reporting after the first hour and never comes back",
        df[df["ts"] < df["ts"].iloc[0] + pd.Timedelta(minutes=60)].reset_index(drop=True),
    ),
}

FLAG_FOR_FAULT = {
    "dropout": "missing_flag",
    "spike": "outlier_flag",
    "drift": "drift_flag",
    "noise": "noise_flag",
    "duplicate_timestamp": "noise_flag",
    "calibration_bias_subtle": None,
    "calibration_bias_extreme": "outlier_flag",
    "replay_attack": "integrity_flag",
    "data_injection": "integrity_flag",
    "sensor_silent": "missing_flag",
}

# One plain sentence per result, for the person watching the panel. The history behind each
# check (what used to go wrong, how it was measured) lives in docs/, not on the screen.
NOTES = {
    "sensor_silent": "A sensor that stops reporting costs trust like any other missing data, so an outage cannot pass unnoticed.",
    "calibration_bias_subtle": (
        "Known limit: a steady offset that stays in the normal range looks like a genuinely warmer unit to a single sensor. "
        "Comparing zones against the product probe is the planned fix."
    ),
    "replay_attack": "Long runs of identical readings suggest replayed or frozen data. The check is off for the vegetable drawer, where repeated readings are normal.",
    "data_injection": "A jump too large for the unit to physically produce is treated as a fabricated reading.",
}


class InjectRequest(BaseModel):
    device_id: str
    fault: FaultName


def _score_dict(score: QualityScore) -> dict:
    return {
        "trust": score.trust_value,
        "label": score.quality_label,
        "flags": {
            "missing": score.missing_flag,
            "outlier": score.outlier_flag,
            "drift": score.drift_flag,
            "noise": score.noise_flag,
            "integrity": score.integrity_flag,
        },
    }


def _points(df: pd.DataFrame) -> list[dict]:
    return [{"ts": ts.isoformat(), "temp_c": round(float(t), 2)} for ts, t in zip(df["ts"], df["temp"])]


def _clean_window(device_id: str) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """A known-clean window and the bounds it was scored over. The bounds travel with it:
    without them a sensor that stops part-way through leaves no gap to measure, which is
    exactly the silence the "Sensor goes silent" fault demonstrates."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT window_start, window_end FROM quality_score
            WHERE device_id = %s AND NOT (missing_flag OR outlier_flag OR drift_flag OR noise_flag OR integrity_flag)
            ORDER BY trust_value DESC, window_start LIMIT 1
            """,
            (device_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "no clean window available - run dq_engine/run_scoring.py first")
        cur.execute(
            "SELECT ts, temp, humidity FROM sensor_reading WHERE device_id = %s AND ts >= %s AND ts < %s ORDER BY ts",
            (device_id, row[0], row[1]),
        )
        return pd.DataFrame(cur.fetchall(), columns=["ts", "temp", "humidity"]), pd.Timestamp(row[0]), pd.Timestamp(row[1])


@router.post("/demo/inject")
def inject(req: InjectRequest, user: Annotated[User, Depends(get_current_user)]):
    if req.device_id not in UNITS:
        raise HTTPException(404, "unknown unit")
    cfg = UNITS[req.device_id]
    interval = EXPECTED_INTERVAL_S.get(req.device_id, DEFAULT_INTERVAL_S)

    clean, w_start, w_end = _clean_window(req.device_id)
    description, faulty = FAULTS[req.fault](clean.copy(), req.device_id)

    before = score_window(req.device_id, clean, interval, window_start=w_start, window_end=w_end)
    after = score_window(req.device_id, faulty, interval, window_start=w_start, window_end=w_end)

    expected_flag = FLAG_FOR_FAULT[req.fault]
    detected = bool(expected_flag and getattr(after, expected_flag))

    with connect() as conn:
        log_action(conn, user, "demo_run", target=cfg.name, detail=f"{req.fault} -> detected={detected}")

    return {
        "fault": req.fault,
        "description": description,
        "unit_name": cfg.name,
        "max_safe_c": cfg.max_safe_c,
        "detected": detected,
        "note": NOTES.get(req.fault),
        "before": {"score": _score_dict(before), "points": _points(clean)},
        "after": {
            "score": _score_dict(after),
            "points": _points(faulty),
            "flagged": {
                reason: sorted({ts.isoformat() for ts in stamps}) for reason, stamps in after.flagged.items() if stamps
            },
        },
    }
