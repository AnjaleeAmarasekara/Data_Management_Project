"""Real-time monitoring: live state per unit and recent readings for charts.

Live trust scores are computed on demand from the newest 30 minutes of readings, using
the same dq_engine code as the batch scorer -- what the dashboard shows is what the
quality layer actually concluded, not a separate re-implementation.
"""

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from analytics.config import UNITS
from analytics.arima import forecast_arima
from backend.app.db import baseline_temp, connect
from dq_engine.config import DEFAULT_INTERVAL_S, EXPECTED_INTERVAL_S, WINDOW_MINUTES
from dq_engine.scoring import score_window

router = APIRouter(tags=["units"])


def _iso(ts) -> str:
    return pd.Timestamp(ts).isoformat()


def _recent_raw(conn, device_id: str, minutes: int) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ts, temp, humidity FROM sensor_reading
            WHERE device_id = %s
              AND ts > (SELECT max(ts) FROM sensor_reading WHERE device_id = %s) - make_interval(mins => %s)
            ORDER BY ts
            """,
            (device_id, device_id, minutes),
        )
        return pd.DataFrame(cur.fetchall(), columns=["ts", "temp", "humidity"])


def _unit_state(conn, device_id: str) -> dict:
    cfg = UNITS[device_id]
    baseline = baseline_temp(device_id)
    raw = _recent_raw(conn, device_id, WINDOW_MINUTES + 15)
    if raw.empty:
        return {"device_id": device_id, "unit_name": cfg.name, "status": "no_data"}

    window = raw[raw["ts"] > raw["ts"].max() - pd.Timedelta(minutes=WINDOW_MINUTES)].reset_index(drop=True)
    score = score_window(device_id, window, EXPECTED_INTERVAL_S.get(device_id, DEFAULT_INTERVAL_S))

    minutely = raw.set_index("ts")["temp"].resample("1min").mean().dropna()
    forecast = forecast_arima(cfg.to_cold(minutely, baseline), cfg.max_safe_c)

    latest = raw.iloc[-1]
    latest_cold = float(cfg.to_cold(latest["temp"], baseline))
    if latest_cold > cfg.max_safe_c:
        status = "critical"
    elif forecast.get("status") == "at_risk" or score.quality_label != "High":
        status = "warning"
    else:
        status = "ok"

    return {
        "device_id": device_id,
        "unit_name": cfg.name,
        "setpoint_c": cfg.setpoint_c,
        "max_safe_c": cfg.max_safe_c,
        "categories": list(cfg.categories),
        "status": status,
        "latest": {"ts": _iso(latest["ts"]), "temp_c": round(latest_cold, 2), "humidity": round(float(latest["humidity"]), 1)},
        "trust": {
            "value": score.trust_value,
            "label": score.quality_label,
            "flags": {
                "missing": score.missing_flag,
                "outlier": score.outlier_flag,
                "drift": score.drift_flag,
                "noise": score.noise_flag,
                "integrity": score.integrity_flag,
            },
        },
        "forecast": forecast,
    }


@router.get("/units")
def list_units():
    with connect() as conn:
        return [_unit_state(conn, device_id) for device_id in UNITS]


@router.get("/units/{device_id}/readings")
def unit_readings(device_id: str, hours: float = Query(6, ge=0.25, le=72), end: str | None = None):
    """Minute-level clean series for the chart, plus the individual readings the data
    quality layer flagged inside the same range (shown as markers)."""
    if device_id not in UNITS:
        raise HTTPException(404, "unknown unit")
    cfg = UNITS[device_id]
    baseline = baseline_temp(device_id)

    with connect() as conn, conn.cursor() as cur:
        if end is None:
            cur.execute("SELECT max(ts) FROM sensor_reading WHERE device_id = %s", (device_id,))
            end_ts = cur.fetchone()[0]
        else:
            end_ts = pd.Timestamp(end).to_pydatetime()
        cur.execute(
            """
            SELECT r.ts, r.temp, r.humidity, f.reason
            FROM sensor_reading r
            LEFT JOIN reading_flag f ON f.device_id = r.device_id AND f.ts = r.ts AND f.reason IN ('outlier', 'noise', 'integrity')
            WHERE r.device_id = %s AND r.ts > %s - make_interval(secs => %s) AND r.ts <= %s
            ORDER BY r.ts
            """,
            (device_id, end_ts, hours * 3600, end_ts),
        )
        df = pd.DataFrame(cur.fetchall(), columns=["ts", "temp", "humidity", "reason"])

    if df.empty:
        return {"device_id": device_id, "points": [], "flags": []}

    flagged = df[df["reason"].notna()].drop_duplicates("ts")  # a reading can carry both outlier and noise
    clean = df[df["reason"].isna()].set_index("ts")[["temp", "humidity"]].resample("1min").mean().dropna(subset=["temp"])
    return {
        "device_id": device_id,
        "unit_name": cfg.name,
        "max_safe_c": cfg.max_safe_c,
        "setpoint_c": cfg.setpoint_c,
        "points": [
            {"ts": _iso(ts), "temp_c": round(float(cfg.to_cold(row.temp, baseline)), 2), "humidity": round(float(row.humidity), 1)}
            for ts, row in clean.iterrows()
        ],
        "flags": [
            {"ts": _iso(row.ts), "temp_c": round(float(cfg.to_cold(row.temp, baseline)), 2), "reason": row.reason}
            for row in flagged.itertuples()
        ],
    }
