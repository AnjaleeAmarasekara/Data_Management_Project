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

import hmac
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, field_validator

from backend.app.db import connect
from backend.app.settings import require_secret

router = APIRouter(tags=["ingest"])

DEVICE_API_KEY = require_secret("DEVICE_API_KEY")


class ReadingIn(BaseModel):
    device_id: str
    temp: float
    humidity: float | None = None
    ts: datetime | None = None  # server stamps it on arrival if the device has no synced clock

    @field_validator("temp", "humidity")
    @classmethod
    def must_be_a_number(cls, v):
        # NaN and infinity are not readings at all, so they are rejected here as a
        # structural error. A finite but implausible value (999 C) is deliberately
        # accepted: judging plausibility is the quality layer's job, and rejecting it at
        # the door would hide the very fault the system exists to detect and score.
        if v is not None and not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v


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
    # constant-time comparison, so response timing reveals nothing about how much of a
    # guessed key was right
    if not hmac.compare_digest((x_device_key or "").encode(), DEVICE_API_KEY.encode()):
        _log_security_event(conn, "auth_failed", None, "invalid or missing X-Device-Key")
        raise HTTPException(401, "Invalid or missing device key")


def _registered(conn, device_ids: set[str]) -> set[str]:
    """The device registry is the sensor_unit table, not the dashboard's demo-unit config.
    Checking the config here once meant every channel outside the demo premises - 856 of
    861 registered channels - was refused and logged as a security incident."""
    with conn.cursor() as cur:
        cur.execute("SELECT device_id FROM sensor_unit WHERE device_id = ANY(%s)", (list(device_ids),))
        return {row[0] for row in cur.fetchall()}


def _validate(conn, readings: list[ReadingIn]) -> list[tuple]:
    known = _registered(conn, {r.device_id for r in readings})
    for r in readings:
        if r.device_id not in known:
            _log_security_event(conn, "unknown_device", r.device_id, "push attempted from an unregistered device_id")
            raise HTTPException(404, f"Unknown device_id '{r.device_id}' -- not a registered sensor")
    now = datetime.now(timezone.utc)
    return [(r.device_id, r.ts or now, r.temp, r.humidity) for r in readings]


# A gateway that loses its acknowledgement resends the same reading. Storing it twice would
# double-count it in every downstream statistic, so an exact repeat - same device, time and
# values - is a no-op. A *different* value at the same timestamp is still stored: that is a
# genuine data-quality signal, and the timestamp-integrity check is there to flag it.
INSERT_IDEMPOTENT = """
    INSERT INTO sensor_reading (device_id, ts, temp, humidity)
    SELECT %(d)s, %(ts)s, %(t)s, %(h)s
    WHERE NOT EXISTS (
        SELECT 1 FROM sensor_reading
        WHERE device_id = %(d)s AND ts = %(ts)s
          AND temp = %(t)s AND humidity IS NOT DISTINCT FROM %(h)s
    )
"""


def _store(conn, rows: list[tuple]) -> int:
    stored = 0
    with conn.cursor() as cur:
        for d, ts, t, h in rows:
            cur.execute(INSERT_IDEMPOTENT, {"d": d, "ts": ts, "t": t, "h": h})
            stored += cur.rowcount
    conn.commit()
    return stored


@router.post("/ingest/readings", status_code=201)
def ingest_reading(reading: ReadingIn, x_device_key: str | None = Header(default=None)):
    """Single reading, the common case: a sensor with a live connection pushing as it samples."""
    with connect() as conn:
        _check_device_key(conn, x_device_key)
        rows = _validate(conn, [reading])
        stored = _store(conn, rows)
    return {"accepted": True, "duplicate": stored == 0, "device_id": rows[0][0], "ts": rows[0][1].isoformat()}


@router.post("/ingest/readings/batch", status_code=201)
def ingest_batch(batch: BatchIn, x_device_key: str | None = Header(default=None)):
    """Multiple readings in one call: a gateway catching up after a network dropout
    (proposal: "Communication and Network Problems") sends its buffered backlog at once."""
    with connect() as conn:
        _check_device_key(conn, x_device_key)
        if not batch.readings:
            raise HTTPException(400, "readings must not be empty")
        rows = _validate(conn, batch.readings)
        stored = _store(conn, rows)
    return {"accepted": len(rows), "stored": stored, "duplicates": len(rows) - stored}
