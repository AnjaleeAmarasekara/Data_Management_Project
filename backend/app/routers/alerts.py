"""Alerts and business impact, both produced by analytics/run_analytics.py."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from analytics.config import UNITS
from backend.app.auth import User, require_role
from backend.app.db import connect

router = APIRouter(tags=["alerts"])

MESSAGES = {
    "excursion": "Temperature stayed above the safe limit for {minutes} min (peak {peak} C)",
    "isolation_forest": "Unusual temperature/humidity behaviour for {minutes} min (peak {peak} C) - early warning",
}


@router.get("/alerts")
def list_alerts(limit: int = Query(50, ge=1, le=500)):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.device_id, e.kind, e.severity, e.start_ts, e.end_ts, e.minutes, e.peak_temp_c, e.avg_trust,
                   a.acknowledged_at, u.full_name, a.note
            FROM anomaly_event e
            LEFT JOIN alert_acknowledgement a ON a.anomaly_event_id = e.id
            LEFT JOIN app_user u ON u.id = a.acknowledged_by
            ORDER BY e.start_ts DESC LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "device_id": r[1],
            "unit_name": UNITS[r[1]].name,
            "kind": r[2],
            "severity": r[3],
            "start_ts": r[4].isoformat(),
            "end_ts": r[5].isoformat(),
            "minutes": r[6],
            "peak_temp_c": r[7],
            "avg_trust": float(r[8]) if r[8] is not None else None,
            "message": MESSAGES[r[2]].format(minutes=r[6], peak=r[7]),
            "acknowledged": r[9] is not None,
            "acknowledged_at": r[9].isoformat() if r[9] else None,
            "acknowledged_by": r[10],
            "acknowledgement_note": r[11],
        }
        for r in rows
    ]


class AcknowledgeRequest(BaseModel):
    note: str | None = None


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int, req: AcknowledgeRequest, user: Annotated[User, Depends(require_role("quality_supervisor"))]):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM anomaly_event WHERE id = %s", (alert_id,))
        if not cur.fetchone():
            raise HTTPException(404, "No such alert")
        cur.execute(
            """
            INSERT INTO alert_acknowledgement (anomaly_event_id, acknowledged_by, note)
            VALUES (%s, %s, %s)
            ON CONFLICT (anomaly_event_id) DO UPDATE SET acknowledged_by = EXCLUDED.acknowledged_by, note = EXCLUDED.note, acknowledged_at = now()
            """,
            (alert_id, user.id, req.note),
        )
        conn.commit()
    return {"acknowledged": True}


@router.get("/impact")
def impact():
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT device_id, relative_day, categories, sales_exposed, excursion_minutes, degree_minutes,
                   peak_temp_c, spoilage_probability, revenue_at_risk, avg_trust
            FROM impact_estimate ORDER BY relative_day, device_id
            """
        )
        rows = cur.fetchall()

    items = [
        {
            "device_id": r[0],
            "unit_name": UNITS[r[0]].name,
            "relative_day": r[1],
            "categories": r[2],
            "sales_exposed": float(r[3]),
            "excursion_minutes": r[4],
            "degree_minutes": r[5],
            "peak_temp_c": r[6],
            "spoilage_probability": r[7],
            "revenue_at_risk": float(r[8]),
            "avg_trust": float(r[9]),
        }
        for r in rows
    ]
    by_unit: dict = {}
    for i in items:
        u = by_unit.setdefault(i["unit_name"], {"unit_name": i["unit_name"], "revenue_at_risk": 0.0, "sales_exposed": 0.0, "excursion_minutes": 0})
        u["revenue_at_risk"] = round(u["revenue_at_risk"] + i["revenue_at_risk"], 2)
        u["sales_exposed"] = round(u["sales_exposed"] + i["sales_exposed"], 2)
        u["excursion_minutes"] += i["excursion_minutes"]

    return {
        "total_revenue_at_risk": round(sum(i["revenue_at_risk"] for i in items), 2),
        "total_sales_exposed": round(sum(i["sales_exposed"] for i in items), 2),
        "by_unit": list(by_unit.values()),
        "rows": items,
    }
