"""Real-time monitoring: live state per unit and recent readings for charts.

Live trust scores are computed on demand from the newest scoring window of readings, using
the same dq_engine code as the batch scorer -- what the dashboard shows is what the
quality layer actually concluded, not a separate re-implementation.
"""

from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from analytics.config import STEP_MINUTES, UnitConfig
from analytics.arima import forecast_arima
from analytics.registry import load_units
from backend.app.db import connect
from dq_engine.config import DEFAULT_INTERVAL_S, EXPECTED_INTERVAL_S, SILENCE_THRESHOLD_MIN, WINDOW_MINUTES
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


def reference_times(conn, device_id: str) -> tuple:
    """(this channel's last reading, its premises' last reading, the fleet's last reading).

    "Latest" used to mean this channel's own last reading, so a sensor that stopped
    reporting kept showing its final values as if they were current, with nothing to say
    they were stale. Comparing against sibling channels and the fleet is what makes a
    silent sensor visible.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT (SELECT max(ts) FROM sensor_reading WHERE device_id = %(d)s),
                   (SELECT max(r.ts) FROM sensor_reading r JOIN sensor_unit u USING (device_id)
                    WHERE u.site_id = (SELECT site_id FROM sensor_unit WHERE device_id = %(d)s)),
                   -- The fleet is the replayed record only. A live probe's readings carry
                   -- today's date; letting them in made every replayed unit look silent
                   -- for the years between its record and today.
                   (SELECT max(r.ts) FROM sensor_reading r JOIN sensor_unit u USING (device_id)
                    WHERE u.clock = 'data')
            """,
            {"d": device_id},
        )
        return cur.fetchone()


def silence(device_last, site_last, fleet_last, now=None) -> dict:
    """How long a channel and its premises have been quiet.

    now=None is the replay rule: a channel is measured against its premises' newest reading
    and the premises against the fleet's, because a historical record has no "now". A live
    unit passes the wall clock instead. Measured against siblings alone, a single live
    probe that lost power would never look silent - it is always level with itself.
    """
    if now is not None:
        channel_min = (now - device_last).total_seconds() / 60 if device_last else None
        site_min = (now - site_last).total_seconds() / 60 if site_last else None
    else:
        channel_min = (site_last - device_last).total_seconds() / 60 if device_last and site_last else None
        site_min = (fleet_last - site_last).total_seconds() / 60 if site_last and fleet_last else None
    return {
        "last_seen": _iso(device_last) if device_last else None,
        "silent_for_min": round(channel_min, 1) if channel_min is not None else None,
        "channel_silent": channel_min is not None and channel_min > SILENCE_THRESHOLD_MIN,
        # every zone of the premises has gone quiet together: the gateway, the network or
        # the power, rather than one sensor
        "premises_silent": site_min is not None and site_min > SILENCE_THRESHOLD_MIN,
        "premises_silent_for_min": round(site_min, 1) if site_min is not None else None,
        "clock": "wall" if now is not None else "data",
    }


def warmup(cfg: UnitConfig, first_ts, now) -> dict | None:
    """Warm-up state for a live unit, or None once it is over (or never applied).

    Counted from the unit's first reading, not from when it was registered: an admin may
    register a fridge days before the probe is fitted, and the probe still needs its time
    to settle once it is."""
    if cfg.clock != "wall" or not cfg.warmup_min or first_ts is None or now is None:
        return None
    ends = pd.Timestamp(first_ts) + pd.Timedelta(minutes=cfg.warmup_min)
    if pd.Timestamp(now) >= ends:
        return None
    return {
        "ends_at": _iso(ends),
        "remaining_min": round((ends - pd.Timestamp(now)).total_seconds() / 60, 1),
        "warmup_min": cfg.warmup_min,
    }


def _first_ts(conn, device_id: str):
    with conn.cursor() as cur:
        cur.execute("SELECT min(ts) FROM sensor_reading WHERE device_id = %s", (device_id,))
        return cur.fetchone()[0]


def _base(device_id: str, cfg: UnitConfig) -> dict:
    return {
        "device_id": device_id,
        "unit_name": cfg.name,
        "setpoint_c": cfg.setpoint_c,
        "max_safe_c": cfg.max_safe_c,
        "min_safe_c": cfg.min_safe_c,
        "categories": list(cfg.categories),
        "clock": cfg.clock,
    }


def _unit_state(conn, device_id: str, cfg: UnitConfig) -> dict:
    device_last, site_last, fleet_last = reference_times(conn, device_id)
    if device_last is None:
        # A registered live unit whose probe has not reported yet. Not an alarm: there is
        # no stock reading to be wrong about, and the card says it is waiting.
        return {**_base(device_id, cfg), "status": "no_data", "breach": None}

    now = datetime.now(timezone.utc) if cfg.clock == "wall" else None
    quiet = silence(device_last, site_last, fleet_last, now)
    first_ts = _first_ts(conn, device_id)
    warming = warmup(cfg, first_ts, now)
    raw = _recent_raw(conn, device_id, WINDOW_MINUTES + 15)

    # The live window ends at "now" for the unit's clock: the premises' newest reading for
    # a replayed unit, the wall clock for a live one. Either way a channel that has gone
    # quiet is scored on a window it has stopped filling, and the missing-data check sees
    # the silence.
    w_end = pd.Timestamp(now if now is not None else site_last)
    w_start = w_end - pd.Timedelta(minutes=WINDOW_MINUTES)
    # A unit installed less than a window ago is judged only on the time it has existed.
    # Otherwise the hours before the probe was fitted count as missing data, and every new
    # installation starts with a Low trust score it did nothing to earn. For a live probe,
    # "existed" starts when warm-up ends: the fall from room temperature is a step change
    # of several degrees per reading, which the noise check rightly calls noise in a
    # settled unit and wrongly calls noise here.
    settled_from = pd.Timestamp(first_ts) + pd.Timedelta(minutes=cfg.warmup_min if cfg.clock == "wall" else 0)
    w_start = max(w_start, settled_from - pd.Timedelta(seconds=1))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ts, temp, humidity FROM sensor_reading WHERE device_id = %s AND ts > %s AND ts <= %s ORDER BY ts",
            (device_id, w_start, w_end),
        )
        window = pd.DataFrame(cur.fetchall(), columns=["ts", "temp", "humidity"])
    # While warming up there is nothing settled to judge yet, and an empty window scores
    # Low; the card says the probe is settling instead of showing a verdict.
    score = None if warming else score_window(
        device_id, window, EXPECTED_INTERVAL_S.get(device_id, DEFAULT_INTERVAL_S), window_start=w_start, window_end=w_end
    )

    sampled = raw.set_index("ts")["temp"].resample(f"{STEP_MINUTES}min").mean().dropna()
    silent = quiet["channel_silent"] or quiet["premises_silent"]
    if silent:
        # a forecast extrapolated from readings that stopped arriving would be fiction
        forecast = {"status": "insufficient_data", "reason": "sensor silent", "horizon_min": None}
    elif warming:
        # the probe is still falling from room temperature; extrapolating that slope would
        # predict a cold breach that is not coming
        forecast = {"status": "insufficient_data", "reason": "warming up", "horizon_min": None}
    else:
        forecast = forecast_arima(sampled, cfg.max_safe_c, min_safe_c=cfg.min_safe_c)

    latest = raw.iloc[-1]
    latest_cold = float(latest["temp"])
    # Both ends of the safe band are failures. A chilled unit sitting below its lower
    # bound is freezing its stock, which used to be reported as "Normal".
    too_warm = latest_cold > cfg.max_safe_c
    too_cold = cfg.min_safe_c is not None and latest_cold < cfg.min_safe_c
    if silent:
        # A sensor that has stopped reporting is the failure a cold-chain monitor most
        # needs to raise: its last temperature says nothing about the stock now. This holds
        # during warm-up too - a probe that dies while settling is still dead.
        status = "critical"
    elif warming:
        status = "warming_up"
    elif too_warm or too_cold:
        status = "critical"
    elif forecast.get("status") == "at_risk" or score.quality_label != "High":
        status = "warning"
    else:
        status = "ok"
    breach = (
        "premises_silent" if quiet["premises_silent"]
        else "silent" if quiet["channel_silent"]
        else None if warming
        else "too_warm" if too_warm
        else "too_cold" if too_cold
        else None
    )

    return {
        **_base(device_id, cfg),
        "status": status,
        "breach": breach,
        "connectivity": quiet,
        "warmup": warming,
        "latest": {"ts": _iso(latest["ts"]), "temp_c": round(latest_cold, 2), "humidity": None if pd.isna(latest["humidity"]) else round(float(latest["humidity"]), 1)},
        "trust": None if score is None else {
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
        return [_unit_state(conn, device_id, cfg) for device_id, cfg in load_units(conn).items()]


@router.get("/units/{device_id}/readings")
def unit_readings(device_id: str, hours: float = Query(6, ge=0.25, le=72), end: str | None = None):
    """Minute-level clean series for the chart, plus the individual readings the data
    quality layer flagged inside the same range (shown as markers)."""
    with connect() as conn, conn.cursor() as cur:
        cfg = load_units(conn).get(device_id)
        if cfg is None:
            raise HTTPException(404, "unknown unit")
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
    clean = (
        df[df["reason"].isna()].set_index("ts")[["temp", "humidity"]]
        .resample(f"{STEP_MINUTES}min").mean().dropna(subset=["temp"])
    )
    return {
        "device_id": device_id,
        "unit_name": cfg.name,
        "max_safe_c": cfg.max_safe_c,
        "min_safe_c": cfg.min_safe_c,
        "setpoint_c": cfg.setpoint_c,
        "points": [
            # a DS18B20 probe measures temperature only, so humidity may be absent
            {"ts": _iso(ts), "temp_c": round(float(row.temp), 2), "humidity": None if pd.isna(row.humidity) else round(float(row.humidity), 1)}
            for ts, row in clean.iterrows()
        ],
        "flags": [
            {"ts": _iso(row.ts), "temp_c": round(float(row.temp), 2), "reason": row.reason}
            for row in flagged.itertuples()
        ],
    }
