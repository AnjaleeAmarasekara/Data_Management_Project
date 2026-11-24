"""Data Quality Status: batch scoring results stored by dq_engine/run_scoring.py."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from analytics.registry import load_units
from backend.app.db import connect
from dq_engine.config import SILENCE_THRESHOLD_MIN

router = APIRouter(tags=["quality"])

EVALUATION_FILE = Path(__file__).resolve().parents[3] / "docs" / "ml_evaluation_quality_layer.json"

# The evaluation speaks in internal flag and fault names; the dashboard speaks to a shop manager.
CHECK_LABELS = {
    "missing_flag": "Missing data",
    "outlier_flag": "Outliers",
    "drift_flag": "Drift",
    "noise_flag": "Noise / timestamps",
    "integrity_flag": "Tampering",
}
FAULT_LABELS = {
    "dropout": "Sensor dropout",
    "spike": "Interference spike",
    "drift": "Sensor drift",
    "noise": "Electrical noise",
    "duplicate_timestamp": "Duplicate timestamp",
    "calibration_bias_subtle": "Calibration error (subtle)",
    "calibration_bias_extreme": "Calibration error (gross)",
    "replay_attack": "Replay attack",
    "data_injection": "Data injection attack",
    "sensor_silent": "Sensor goes silent",
}


@router.get("/quality/summary")
def quality_summary():
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT device_id,
                   count(*) AS windows,
                   count(*) FILTER (WHERE quality_label = 'High')   AS high,
                   count(*) FILTER (WHERE quality_label = 'Medium') AS medium,
                   count(*) FILTER (WHERE quality_label = 'Low')    AS low,
                   round(avg(trust_value), 1) AS avg_trust,
                   count(*) FILTER (WHERE missing_flag)   AS missing_windows,
                   count(*) FILTER (WHERE outlier_flag)   AS outlier_windows,
                   count(*) FILTER (WHERE drift_flag)     AS drift_windows,
                   count(*) FILTER (WHERE noise_flag)     AS noise_windows,
                   count(*) FILTER (WHERE integrity_flag) AS integrity_windows
            FROM quality_score GROUP BY device_id
            """
        )
        score_rows = {r[0]: r for r in cur.fetchall()}

        cur.execute("SELECT device_id, count(*) FROM sensor_reading GROUP BY device_id")
        reading_counts = dict(cur.fetchall())

        cur.execute("SELECT device_id, reason, count(*) FROM reading_flag GROUP BY 1, 2")
        flag_counts: dict = {}
        for device_id, reason, n in cur.fetchall():
            flag_counts.setdefault(device_id, {})[reason] = n
        registry = load_units(conn)

    units = []
    for device_id, cfg in registry.items():
        r = score_rows.get(device_id)
        if not r:
            continue
        flags = flag_counts.get(device_id, {})
        excluded = flags.get("outlier", 0) + flags.get("noise", 0) + flags.get("integrity", 0)
        units.append(
            {
                "device_id": device_id,
                "unit_name": cfg.name,
                "readings": reading_counts.get(device_id, 0),
                "windows": r[1],
                "labels": {"High": r[2], "Medium": r[3], "Low": r[4]},
                "avg_trust": float(r[5]),
                "windows_flagged": {"missing": r[6], "outlier": r[7], "drift": r[8], "noise": r[9], "integrity": r[10]},
                "flagged_readings": flags,
                "excluded_from_analytics": excluded,
            }
        )
    return {"units": units}


@router.get("/quality/timeline")
def quality_timeline(device_id: str, limit: int = Query(500, ge=1, le=2000)):
    with connect() as conn, conn.cursor() as cur:
        if device_id not in load_units(conn):
            raise HTTPException(404, "unknown unit")
        cur.execute(
            """
            SELECT window_start, trust_value, quality_label, missing_flag, outlier_flag, drift_flag, noise_flag, integrity_flag
            FROM quality_score WHERE device_id = %s ORDER BY window_start LIMIT %s
            """,
            (device_id, limit),
        )
        rows = cur.fetchall()
    return [
        {
            "window_start": r[0].isoformat(),
            "trust": float(r[1]),
            "label": r[2],
            "flags": {"missing": r[3], "outlier": r[4], "drift": r[5], "noise": r[6], "integrity": r[7]},
        }
        for r in rows
    ]


@router.get("/quality/network")
def quality_network(limit: int = Query(200, ge=1, le=1000)):
    """Fleet-wide view: every instrumented premises, not just the demo one.

    The quality layer scores all 123 refrigerators, but until now only the demo shop was
    reachable from the dashboard, so the system's main scalability evidence existed
    nowhere a reviewer could see it. This endpoint exposes it: one row per premises, worst
    zones first, so a manager looking after a chain sees where to go first.
    """
    with connect() as conn, conn.cursor() as cur:
        # Silence first: which channels have stopped reporting, measured against their
        # premises, and which premises have gone quiet altogether, measured against the
        # fleet. A whole premises going silent points at its gateway, network or power.
        cur.execute(
            """
            WITH dev AS (SELECT device_id, max(ts) AS last_ts FROM sensor_reading GROUP BY device_id),
                 site AS (SELECT u.site_id, max(d.last_ts) AS last_ts
                          FROM dev d JOIN sensor_unit u USING (device_id) GROUP BY u.site_id),
                 fleet AS (SELECT max(last_ts) AS last_ts FROM site)
            SELECT s.site_id,
                   count(*) FILTER (WHERE s.last_ts - d.last_ts > make_interval(mins => %(t)s)),
                   (SELECT last_ts FROM fleet) - s.last_ts > make_interval(mins => %(t)s),
                   s.last_ts
            FROM site s JOIN sensor_unit u ON u.site_id = s.site_id JOIN dev d ON d.device_id = u.device_id
            GROUP BY s.site_id, s.last_ts
            """,
            {"t": SILENCE_THRESHOLD_MIN},
        )
        quiet = {r[0]: {"silent_channels": r[1], "premises_silent": r[2], "last_seen": r[3].isoformat()} for r in cur.fetchall()}

        cur.execute(
            """
            SELECT u.site_id,
                   max(u.site_name)                                        AS site_name,
                   count(DISTINCT u.device_id) FILTER (
                       WHERE u.unit_type NOT IN ('product_probe', 'ambient')
                   )                                                       AS units,
                   count(q.*)                                              AS windows,
                   round(avg(q.trust_value), 1)                            AS avg_trust,
                   count(*) FILTER (WHERE q.quality_label = 'Low')         AS low_windows,
                   count(*) FILTER (WHERE q.integrity_flag)                AS integrity_windows
            FROM sensor_unit u
            LEFT JOIN quality_score q ON q.device_id = u.device_id
            GROUP BY u.site_id
            ORDER BY avg_trust NULLS LAST, low_windows DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        cur.execute(
            """
            SELECT count(DISTINCT site_id),
                   count(*) FILTER (WHERE unit_type NOT IN ('product_probe', 'ambient')),
                   count(*),
                   (SELECT count(*) FROM sensor_reading), (SELECT count(*) FROM quality_score)
            FROM sensor_unit
            """
        )
        sites, cold_units, channels, readings, windows = cur.fetchone()

    return {
        # Cold units and channels are counted separately: the product probe and the
        # ambient sensor are monitored and scored, but they are not cold-storage units and
        # reporting them as such would inflate the headline figure.
        "totals": {
            "silent_channels": sum(v["silent_channels"] for v in quiet.values()),
            "silent_premises": sum(1 for v in quiet.values() if v["premises_silent"]),
            "sites": sites,
            "cold_units": cold_units,
            "channels": channels,
            "readings": readings,
            "windows": windows,
        },
        "sites": [
            {
                "site_id": r[0],
                "site_name": r[1],
                "units": r[2],
                "windows": r[3],
                "avg_trust": float(r[4]) if r[4] is not None else None,
                "low_windows": r[5],
                "integrity_windows": r[6],
                **quiet.get(r[0], {"silent_channels": 0, "premises_silent": False, "last_seen": None}),
            }
            for r in rows
        ],
        "silence_threshold_min": SILENCE_THRESHOLD_MIN,
    }


@router.get("/quality/evaluation")
def quality_evaluation():
    """How accurate the quality layer is, in the standard classification metrics.

    The numbers are produced offline by dq_engine/evaluation/evaluate_quality.py, which injects
    known faults into clean real windows and scores them with the production code. They are served
    here rather than recomputed per request because the run takes minutes: this endpoint reports a
    measurement, and the file records when it was taken so a stale one is visible rather than
    implied to be current.
    """
    if not EVALUATION_FILE.exists():
        raise HTTPException(404, "no evaluation on file - run python -m dq_engine.evaluation.evaluate_quality")
    report = json.loads(EVALUATION_FILE.read_text(encoding="utf-8"))

    # The window-level figure answers the question a user actually asks ("is anything wrong with
    # this window?"), so it is the headline. The label-level figure is reported beside it because
    # the two differ for a reason worth showing: an isolated fault is excluded at row level without
    # pulling the window below High, which is by design, not a miss.
    overall = report["window_level_overall"]
    label_level = report["window_level_overall_label_level"]
    return {
        "generated_at": report["generated_at"],
        "method": report["method"],
        "sample": report["sample"],
        "overall": {k: overall[k] for k in ("tp", "fp", "fn", "tn", "precision", "recall", "f1", "accuracy")},
        "label_level": {k: label_level[k] for k in ("precision", "recall", "f1", "accuracy")},
        "false_alarm_rate": report["false_alarm_rate_on_real_windows"],
        "false_alarm_rate_label_level": report["false_alarm_rate_label_level"],
        "macro_average": report["macro_average_detectable_faults"],
        "per_check": [
            {"check": CHECK_LABELS.get(name, name), "catches": [FAULT_LABELS.get(f, f) for f in v["catches"]],
             **{k: v[k] for k in ("windows", "precision", "recall", "f1", "accuracy")}}
            for name, v in report["per_check"].items()
        ],
        "per_fault": [
            {"fault": FAULT_LABELS.get(name, name), "expected_check": CHECK_LABELS.get(v["expected_flag"], None),
             **{k: v[k] for k in ("windows", "recall", "precision", "f1")}}
            for name, v in report["per_fault"].items()
        ],
    }
