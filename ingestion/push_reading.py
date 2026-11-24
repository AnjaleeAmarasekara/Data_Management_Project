"""Push one reading to the ingestion API, as a live probe would. For manual testing.

    python ingestion/push_reading.py LIVE-001-MS 23.5                  # stamped now
    python ingestion/push_reading.py LIVE-001-MS 4.0 --minutes-ago 50  # back-dated

Uses the same endpoint and device key (DEVICE_API_KEY from .env) as a real device, so a
refusal here is a refusal a real probe would get too.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.app.settings import require_secret  # noqa: E402

API_BASE = os.environ.get("API_BASE", "http://localhost:8000/api")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("device_id")
    ap.add_argument("temp", type=float)
    ap.add_argument("--humidity", type=float, default=55.0)
    ap.add_argument("--minutes-ago", type=float, default=0, help="back-date the reading")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc) - timedelta(minutes=args.minutes_ago)
    r = requests.post(
        f"{API_BASE}/ingest/readings",
        json={"device_id": args.device_id, "temp": args.temp, "humidity": args.humidity, "ts": ts.isoformat()},
        headers={"X-Device-Key": require_secret("DEVICE_API_KEY")},
        timeout=10,
    )
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
