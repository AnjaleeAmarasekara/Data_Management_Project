"""Data Quality Status: batch scoring results stored by dq_engine/run_scoring.py."""

from fastapi import APIRouter, HTTPException, Query

from analytics.config import UNITS
from backend.app.db import connect

router = APIRouter(tags=["quality"])


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

    units = []
    for device_id, cfg in UNITS.items():
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
    if device_id not in UNITS:
        raise HTTPException(404, "unknown unit")
    with connect() as conn, conn.cursor() as cur:
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
