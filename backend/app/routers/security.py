"""Security events: unauthorized/unknown-device attempts logged by the ingestion API.

This is the intrusion-detection half of the story (who tried to get in); the data-
tampering half (is accepted data trustworthy) is dq_engine/checks/integrity.py, surfaced
through quality_score.integrity_flag like any other data quality flag.
"""

from fastapi import APIRouter, Query

from backend.app.db import connect

router = APIRouter(tags=["security"])


@router.get("/security/events")
def list_security_events(limit: int = Query(100, ge=1, le=1000)):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, event_type, device_id, detail, occurred_at
            FROM security_event ORDER BY occurred_at DESC LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

        cur.execute(
            """
            SELECT event_type, count(*)
            FROM security_event WHERE occurred_at > now() - interval '24 hours'
            GROUP BY event_type
            """
        )
        last_24h = dict(cur.fetchall())

    return {
        "total": sum(last_24h.values()),
        "last_24h_by_type": last_24h,
        "events": [
            {
                "id": r[0],
                "event_type": r[1],
                "device_id": r[2],
                "detail": r[3],
                "occurred_at": r[4].isoformat(),
            }
            for r in rows
        ],
    }
