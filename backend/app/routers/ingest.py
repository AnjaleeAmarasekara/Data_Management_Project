"""Sensor data ingestion -- the "Sensor Data Stream" arrow in the architecture diagram
(IoT Sensors -> Data Quality Layer), kept distinct from retail data's "Batch Upload" arrow.

This is what a real IoT gateway would call to push one reading as it happens. It
authenticates with a device API key (X-Device-Key), not a user JWT: a fridge sensor isn't
a dashboard user, so it sits in a separate trust domain from backend/app/auth.py's
role hierarchy -- the same split a real deployment would have (e.g. device certificates
vs. a human login system).

Deliberately permissive on the data itself: this layer checks that a reading is
*structurally* valid (known device, right types), not that it's *trustworthy*. Judging
trustworthiness is the Data Quality layer's job, downstream, on whatever lands in
sensor_reading -- accepting a bad reading here and catching it later is the point of the
architecture, not a gap in this one.

What this layer *does* police is who's allowed to push at all: every failed device-key
check and every attempt from an unregistered device_id is logged to security_event. That
log is the intrusion-detection half of the story -- unauthorized access attempts, as
distinct from judging whether accepted data is trustworthy (see backend/app/routers/
security.py and dq_engine/checks/integrity.py for the data-tampering half).
"""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from analytics.config import UNITS
from backend.app.db import connect

router = APIRouter(tags=["ingest"])

DEVICE_API_KEY = os.environ.get("DEVICE_API_KEY", "dev-only-device-key-change-me")


class ReadingIn(BaseModel):
    device_id: str
    temp: float
    humidity: float | None = None
    ts: datetime | None = None  # server stamps it on arrival if the device has no synced clock


class BatchIn(BaseModel):
    readings: list[ReadingIn]


def _log_security_event(conn, event_type: str, device_id: str | None, detail: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_event (event_type, device_id, detail) VALUES (%s, %s, %s)",
            (event_type, device_id, detail),
        )
    conn.commit()


def _check_device_key(conn, x_device_key: str | None) -> None:
    if x_device_key != DEVICE_API_KEY:
        _log_security_event(conn, "auth_failed", None, "invalid or missing X-Device-Key")
        raise HTTPException(401, "Invalid or missing device key")


def _validate(conn, r: ReadingIn) -> tuple:
    if r.device_id not in UNITS:
        _log_security_event(conn, "unknown_device", r.device_id, "push attempted from an unregistered device_id")
        raise HTTPException(404, f"Unknown device_id '{r.device_id}' -- not a registered cold-storage unit")
    return (r.device_id, r.ts or datetime.now(timezone.utc), r.temp, r.humidity)


@router.post("/ingest/readings", status_code=201)
def ingest_reading(reading: ReadingIn, x_device_key: str | None = Header(default=None)):
    """Single reading, the common case: a sensor with a live connection pushing as it samples."""
    with connect() as conn:
        _check_device_key(conn, x_device_key)
        row = _validate(conn, reading)
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sensor_reading (device_id, ts, temp, humidity) VALUES (%s, %s, %s, %s)", row)
        conn.commit()
    return {"accepted": True, "device_id": row[0], "ts": row[1].isoformat()}


@router.post("/ingest/readings/batch", status_code=201)
def ingest_batch(batch: BatchIn, x_device_key: str | None = Header(default=None)):
    """Multiple readings in one call: a gateway catching up after a network dropout
    (proposal: "Communication and Network Problems") sends its buffered backlog at once."""
    with connect() as conn:
        _check_device_key(conn, x_device_key)
        if not batch.readings:
            raise HTTPException(400, "readings must not be empty")
        rows = [_validate(conn, r) for r in batch.readings]
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO sensor_reading (device_id, ts, temp, humidity) VALUES (%s, %s, %s, %s)", rows)
        conn.commit()
    return {"accepted": len(rows)}
