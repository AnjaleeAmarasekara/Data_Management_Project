"""Fault-injection demo for the review.

Takes a known-clean real window, corrupts an in-memory copy with a chosen synthetic
fault (the same helpers the test suite uses), and runs the real quality layer on both
versions. Nothing is written to the database.
"""

from typing import Literal

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from analytics.config import UNITS
from backend.app.db import baseline_temp, connect
from dq_engine.config import DEFAULT_INTERVAL_S, EXPECTED_INTERVAL_S
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
    "replay_attack", "data_injection",
]

FAULTS = {
    "dropout": ("Communication dropout: about 5 minutes of readings lost", lambda df: drop_rows(df, len(df) // 3, 60)),
    "spike": ("Interference spike: one reading jumps by +8 C", lambda df: inject_spike(df, len(df) // 2, 8.0)),
    "drift": ("Sensor drift: reading creeps up ~4 C across the window", lambda df: inject_drift(df, 4.0 / len(df))),
    "noise": ("Electrical noise: 30% of readings jump by +/-3 C", lambda df: inject_noise(df, 3.0, 0.3)),
    "duplicate_timestamp": ("Network retransmission: a duplicated timestamp", lambda df: inject_duplicate_timestamp(df, len(df) // 2)),
    "calibration_bias_subtle": ("Calibration error: every reading offset by +6 C (still physically plausible)", lambda df: inject_calibration_bias(df, 6.0)),
    "calibration_bias_extreme": ("Gross calibration error: every reading offset by +60 C", lambda df: inject_calibration_bias(df, 60.0)),
    "replay_attack": ("Replay/spoofing attack: a block of readings frozen to one exact value", lambda df: inject_replay(df, len(df) // 3, 50)),
    "data_injection": ("Data injection attack: one reading jumps further than physically possible", lambda df: inject_impossible_jump(df, len(df) // 2, 25.0)),
}

FLAG_FOR_FAULT = {
    "dropout": "missing_flag",
    "spike": "outlier_flag",
    "drift": "drift_flag",
    "noise": "noise_flag",
    "duplicate_timestamp": "noise_flag",
    "calibration_bias_subtle": None,
    "calibration_bias_extreme": "outlier_flag",
    "replay_attack": None,
    "data_injection": "integrity_flag",
}

NOTES = {
    "calibration_bias_subtle": (
        "Known limitation: a constant offset that stays in the plausible range shifts the whole window "
        "uniformly, so single-sensor statistics cannot tell it from a genuinely warmer unit. Catching it "
        "needs cross-sensor comparison or periodic reference calibration (future work)."
    ),
    "replay_attack": (
        "Known limitation, found by validating against real data: this sensor hardware caches a reading "
        "between its own internal sampling cycles, so exact consecutive-value repeats cover a median of "
        "about 83% of readings naturally -- no threshold separates that from an injected frozen value using "
        "this sensor's data alone. The check exists and is tested in isolation; it's excluded from the live "
        "trust score because it doesn't hold up against this hardware's real behaviour."
    ),
    "data_injection": (
        "A jump bigger than the unit's thermal mass could produce in one sample is treated as a confirmed "
        "integrity failure, not a statistical nuisance -- the same reasoning anomaly-based intrusion "
        "detection applies to a single fabricated reading."
    ),
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


def _points(df: pd.DataFrame, cfg, baseline: float) -> list[dict]:
    return [{"ts": ts.isoformat(), "temp_c": round(float(cfg.to_cold(t, baseline)), 2)} for ts, t in zip(df["ts"], df["temp"])]


def _clean_window(device_id: str) -> pd.DataFrame:
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
            "SELECT ts, temp, humidity FROM sensor_reading WHERE device_id = %s AND ts BETWEEN %s AND %s ORDER BY ts",
            (device_id, row[0], row[1]),
        )
        return pd.DataFrame(cur.fetchall(), columns=["ts", "temp", "humidity"])


@router.post("/demo/inject")
def inject(req: InjectRequest):
    if req.device_id not in UNITS:
        raise HTTPException(404, "unknown unit")
    cfg = UNITS[req.device_id]
    baseline = baseline_temp(req.device_id)
    interval = EXPECTED_INTERVAL_S.get(req.device_id, DEFAULT_INTERVAL_S)

    clean = _clean_window(req.device_id)
    description, apply_fault = FAULTS[req.fault]
    faulty = apply_fault(clean.copy())

    before = score_window(req.device_id, clean, interval)
    after = score_window(req.device_id, faulty, interval)

    expected_flag = FLAG_FOR_FAULT[req.fault]
    detected = bool(expected_flag and getattr(after, expected_flag))

    return {
        "fault": req.fault,
        "description": description,
        "unit_name": cfg.name,
        "max_safe_c": cfg.max_safe_c,
        "detected": detected,
        "note": NOTES.get(req.fault),
        "before": {"score": _score_dict(before), "points": _points(clean, cfg, baseline)},
        "after": {
            "score": _score_dict(after),
            "points": _points(faulty, cfg, baseline),
            "flagged": {
                reason: sorted({ts.isoformat() for ts in stamps}) for reason, stamps in after.flagged.items() if stamps
            },
        },
    }
