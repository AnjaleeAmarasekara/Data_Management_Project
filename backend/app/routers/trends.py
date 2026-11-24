"""Historical trends: how each unit behaved over the whole record, not just the last few hours.

The proposal's dashboard objective promises "past patterns". The monitoring chart only reaches
back 72 hours from the latest reading and shows raw readings, so it answers "what is happening
now" rather than "how has this unit been behaving". This router aggregates the full record into
hourly, six-hourly or daily buckets with TimescaleDB's time_bucket - the operation that database
exists to do quickly - and reports, per bucket, the temperature range, time outside the safe band
in both directions, how much of the expected data arrived, and how far the data can be trusted.

Readings the quality layer excluded are left out of every aggregate, the same rule analytics
follows, and the count of excluded readings is reported alongside so nothing disappears silently.

One thing this page deliberately does not do is profile by time of day. The source loggers record
elapsed minutes, not clock time, and the record is anchored to midnight purely to align it with
the sales calendar. A "warmest at 2 pm" pattern would be an artefact of that anchor.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from analytics.config import PRODUCT_PROBE_FOR, STEP_MINUTES
from analytics.registry import load_units
from backend.app.db import connect

router = APIRouter(tags=["trends"])

BUCKETS = {"hour": "1 hour", "6h": "6 hours", "day": "1 day"}
BUCKET_MINUTES = {"hour": 60, "6h": 360, "day": 1440}
DROP_REASONS = ["outlier", "noise", "integrity"]  # the same exclusions analytics applies


def _bucketed(cur, device_id: str, bucket: str, start, end, max_safe: float, min_safe: float | None) -> dict:
    """Aggregates for one channel, keyed by bucket start."""
    cur.execute(
        """
        SELECT time_bucket(%(b)s::interval, r.ts) AS bucket,
               min(r.temp), avg(r.temp), max(r.temp), avg(r.humidity), count(*),
               count(*) FILTER (WHERE r.temp > %(hi)s),
               count(*) FILTER (WHERE %(lo)s::float IS NOT NULL AND r.temp < %(lo)s::float)
        FROM sensor_reading r
        WHERE r.device_id = %(d)s AND r.ts >= %(s)s AND r.ts <= %(e)s
          AND NOT EXISTS (SELECT 1 FROM reading_flag f
                          WHERE f.device_id = r.device_id AND f.ts = r.ts AND f.reason = ANY(%(drop)s))
        GROUP BY bucket ORDER BY bucket
        """,
        {"b": BUCKETS[bucket], "d": device_id, "s": start, "e": end, "hi": max_safe, "lo": min_safe, "drop": DROP_REASONS},
    )
    return {r[0]: r[1:] for r in cur.fetchall()}


def _range(cur, device_id: str, start: datetime | None, end: datetime | None) -> tuple:
    cur.execute("SELECT min(ts), max(ts) FROM sensor_reading WHERE device_id = %s", (device_id,))
    first, last = cur.fetchone()
    if first is None:
        raise HTTPException(404, "no readings for this unit")
    return start or first, end or last


@router.get("/trends")
def trends(
    device_id: str,
    bucket: str = Query("6h", pattern="^(hour|6h|day)$"),
    start: datetime | None = None,
    end: datetime | None = None,
):
    site = device_id.rsplit("-", 1)[0]
    probe_id = PRODUCT_PROBE_FOR.get(device_id)
    ambient_id = f"{site}-AM"

    with connect() as conn, conn.cursor() as cur:
        cfg = load_units(conn).get(device_id)
        if cfg is None:
            raise HTTPException(404, "unknown unit")
        start, end = _range(cur, device_id, start, end)
        air = _bucketed(cur, device_id, bucket, start, end, cfg.max_safe_c, cfg.min_safe_c)
        product = _bucketed(cur, probe_id, bucket, start, end, cfg.max_safe_c, cfg.min_safe_c) if probe_id else {}
        ambient = _bucketed(cur, ambient_id, bucket, start, end, 999.0, None)

        cur.execute(
            """
            SELECT time_bucket(%(b)s::interval, window_start), avg(trust_value)
            FROM quality_score WHERE device_id = %(d)s AND window_start >= %(s)s AND window_start <= %(e)s
            GROUP BY 1
            """,
            {"b": BUCKETS[bucket], "d": device_id, "s": start, "e": end},
        )
        trust = {r[0]: float(r[1]) for r in cur.fetchall()}

        cur.execute(
            """
            SELECT time_bucket(%(b)s::interval, ts), count(DISTINCT ts) FROM reading_flag
            WHERE device_id = %(d)s AND reason = ANY(%(drop)s) AND ts >= %(s)s AND ts <= %(e)s
            GROUP BY 1
            """,
            {"b": BUCKETS[bucket], "d": device_id, "s": start, "e": end, "drop": DROP_REASONS},
        )
        excluded = dict(cur.fetchall())

    def expected_readings(b) -> float:
        """Readings that should have arrived in this bucket. The first and last buckets are
        clipped to the observed period: judging a final bucket that covers two hours against a
        full day would report the record as 15% incomplete when nothing was missing."""
        b_end = b + timedelta(minutes=BUCKET_MINUTES[bucket])
        covered = (min(b_end, end) - max(b, start)).total_seconds() / 60
        return max(covered / STEP_MINUTES, 1.0)

    points = []
    for b, (tmin, tmean, tmax, hmean, n, warm, cold) in air.items():
        expected = expected_readings(b)
        points.append(
            {
                "bucket_start": b.isoformat(),
                "temp_min": round(float(tmin), 2),
                "temp_mean": round(float(tmean), 2),
                "temp_max": round(float(tmax), 2),
                "humidity_mean": round(float(hmean), 1) if hmean is not None else None,
                "readings": n,
                "excluded": excluded.get(b, 0),
                # readings that arrived, plus ones received but excluded, against what should have
                "completeness": round(min(1.0, (n + excluded.get(b, 0)) / expected), 3),
                "minutes_too_warm": warm * STEP_MINUTES,
                "minutes_too_cold": cold * STEP_MINUTES,
                "avg_trust": round(trust[b], 1) if b in trust else None,
                "product_mean": round(float(product[b][1]), 2) if b in product else None,
                "ambient_mean": round(float(ambient[b][1]), 2) if b in ambient else None,
            }
        )

    return {
        "device_id": device_id,
        "unit_name": cfg.name,
        "bucket": bucket,
        "max_safe_c": cfg.max_safe_c,
        "min_safe_c": cfg.min_safe_c,
        "has_product_probe": bool(product),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "points": points,
        "totals": {
            "readings": sum(p["readings"] for p in points),
            "excluded": sum(p["excluded"] for p in points),
            "minutes_too_warm": sum(p["minutes_too_warm"] for p in points),
            "minutes_too_cold": sum(p["minutes_too_cold"] for p in points),
            "completeness": round(
                min(1.0, sum(p["readings"] + p["excluded"] for p in points) / sum(expected_readings(b) for b in air)), 3
            ) if points else None,
        },
    }


@router.get("/trends/overview")
def trends_overview(bucket: str = Query("day", pattern="^(hour|6h|day)$")):
    """Every unit side by side: time spent outside its safe band, per bucket, both directions.
    The quickest way to see which unit is the chronic problem and whether it is getting worse."""
    out = []
    with connect() as conn, conn.cursor() as cur:
        for device_id, cfg in load_units(conn).items():
            try:
                start, end = _range(cur, device_id, None, None)
            except HTTPException:
                # a registered unit whose probe has not reported yet has no history to
                # compare; it must not take the whole comparison down with it
                continue
            series = _bucketed(cur, device_id, bucket, start, end, cfg.max_safe_c, cfg.min_safe_c)
            out.append(
                {
                    "device_id": device_id,
                    "unit_name": cfg.name,
                    "points": [
                        {
                            "bucket_start": b.isoformat(),
                            "minutes_too_warm": v[5] * STEP_MINUTES,
                            "minutes_too_cold": v[6] * STEP_MINUTES,
                            "temp_mean": round(float(v[1]), 2),
                        }
                        for b, v in series.items()
                    ],
                }
            )
    return {"bucket": bucket, "units": out}
