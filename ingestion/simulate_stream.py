"""Replay already-loaded historical sensor readings as a live feed, for demos.

Takes a slice of one device's history, shifts its timestamps so the slice ends "now",
and pushes the rows to the real ingestion API (backend/app/routers/ingest.py) at
accelerated speed -- standing in for a physical sensor's gateway, which is what the
architecture's "Sensor Data Stream" path is for. This calls the same HTTP endpoint a
real device would, authenticated the same way (a device API key, not a user login) --
it does not write to the database directly, so it exercises the actual ingestion path
rather than bypassing it.

Reading the historical slice to replay still goes straight to the database: that's
just sourcing demo content, not part of the "device pushes a reading" story the rest of
this script demonstrates.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import requests

# Runnable as a plain script from anywhere: put the project root on the path so the shared
# analytics config resolves, the same way the scoring and analytics scripts do.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.config import STEP_MINUTES  # noqa: E402
from backend.app.settings import require_secret  # noqa: E402
from db import DATABASE_URL  # noqa: E402

API_BASE = os.environ.get("API_BASE", "http://localhost:8000/api")
# Same rule as the API: no placeholder fallback. A wrong key would only earn a 401, but it
# would also be logged as an intrusion attempt, which is noise in the security log.
DEVICE_API_KEY = require_secret("DEVICE_API_KEY")


def push_reading(device_id: str, ts: datetime, temp: float, humidity: float) -> None:
    resp = requests.post(
        f"{API_BASE}/ingest/readings",
        json={"device_id": device_id, "ts": ts.isoformat(), "temp": temp, "humidity": humidity},
        headers={"X-Device-Key": DEVICE_API_KEY},
        timeout=5,
    )
    resp.raise_for_status()


def replay(device_id: str, hours: float, speed: float) -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ts, temp, humidity FROM sensor_reading
                WHERE device_id = %s
                ORDER BY ts DESC
                LIMIT %s
                """,
                (device_id, max(1, int(hours * 60 / STEP_MINUTES))),  # loggers report every STEP_MINUTES
            )
            rows = cur.fetchall()[::-1]

    if not rows:
        raise SystemExit(f"No historical readings found for device {device_id}")

    offset = datetime.now(timezone.utc) - rows[0][0]

    prev_ts = None
    for ts, temp, humidity in rows:
        if prev_ts is not None:
            time.sleep(max(0.0, (ts - prev_ts).total_seconds() / speed))
        shifted_ts = ts + offset
        try:
            push_reading(device_id, shifted_ts, temp, humidity)
        except requests.RequestException as exc:
            print(f"  !! push failed for {shifted_ts.isoformat()}: {exc}")
            continue
        prev_ts = ts
        print(f"  pushed {shifted_ts.isoformat()}  temp={temp:.1f}  humidity={humidity:.1f}", flush=True)


HISTORY_END = "2018-01-01"  # the loaded refrigerator record ends in 2017; anything later is replayed data


def cleanup() -> None:
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sensor_reading WHERE ts > %s", (HISTORY_END,))
        print(f"removed {cur.rowcount} replayed readings")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay historical sensor readings as a live feed, via the real ingestion API")
    parser.add_argument("device_id", nargs="?", help="device_id from sensor_unit, e.g. BKK-012-MS")
    parser.add_argument("--hours", type=float, default=4.0, help="hours of history to replay")
    parser.add_argument("--speed", type=float, default=60.0, help="playback speed multiplier (5-min samples, so >1 is needed for a live demo)")
    parser.add_argument("--cleanup", action="store_true", help="delete previously replayed readings and exit")
    args = parser.parse_args()
    if args.cleanup:
        cleanup()
        return
    if not args.device_id:
        parser.error("device_id is required unless --cleanup is given")
    replay(args.device_id, args.hours, args.speed)


if __name__ == "__main__":
    main()
