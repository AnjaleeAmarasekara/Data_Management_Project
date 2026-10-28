"""Combined quality + alerts + business-impact report for a date range, as a JSON
preview or a PDF export. Restricted to Quality Supervisor and above -- see auth.py.
"""

import io
from datetime import datetime

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from analytics.config import UNITS
from backend.app.db import connect

router = APIRouter(tags=["reports"])


def _sensor_day_zero(conn) -> pd.Timestamp:
    with conn.cursor() as cur:
        cur.execute("SELECT min(ts) FROM sensor_reading")
        return pd.Timestamp(cur.fetchone()[0]).floor("D")


def _relative_day_range(conn, start: datetime, end: datetime) -> tuple[int, int]:
    day_zero = _sensor_day_zero(conn)
    d0 = (pd.Timestamp(start).floor("D") - day_zero).days + 1
    d1 = (pd.Timestamp(end).floor("D") - day_zero).days + 1
    return d0, d1


def gather_report(conn, start: datetime, end: datetime, device_ids: list[str] | None) -> dict:
    ids = device_ids or list(UNITS.keys())
    d0, d1 = _relative_day_range(conn, start, end)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT device_id, count(*), round(avg(trust_value), 1),
                   count(*) FILTER (WHERE quality_label = 'High'),
                   count(*) FILTER (WHERE quality_label = 'Medium'),
                   count(*) FILTER (WHERE quality_label = 'Low')
            FROM quality_score
            WHERE device_id = ANY(%s) AND window_start BETWEEN %s AND %s
            GROUP BY device_id
            """,
            (ids, start, end),
        )
        quality = {r[0]: {"windows": r[1], "avg_trust": float(r[2]), "high": r[3], "medium": r[4], "low": r[5]} for r in cur.fetchall()}

        cur.execute(
            """
            SELECT device_id, kind, severity, start_ts, minutes, peak_temp_c, avg_trust
            FROM anomaly_event
            WHERE device_id = ANY(%s) AND start_ts BETWEEN %s AND %s
            ORDER BY start_ts
            """,
            (ids, start, end),
        )
        alerts = [
            {
                "device_id": r[0], "unit_name": UNITS[r[0]].name, "kind": r[1], "severity": r[2],
                "start_ts": r[3].isoformat(), "minutes": r[4], "peak_temp_c": r[5],
                "avg_trust": float(r[6]) if r[6] is not None else None,
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT device_id, relative_day, categories, sales_exposed, excursion_minutes,
                   spoilage_probability, revenue_at_risk, avg_trust
            FROM impact_estimate
            WHERE device_id = ANY(%s) AND relative_day BETWEEN %s AND %s
            ORDER BY relative_day, device_id
            """,
            (ids, d0, d1),
        )
        impact = [
            {
                "device_id": r[0], "unit_name": UNITS[r[0]].name, "relative_day": r[1], "categories": r[2],
                "sales_exposed": float(r[3]), "excursion_minutes": r[4], "spoilage_probability": r[5],
                "revenue_at_risk": float(r[6]), "avg_trust": float(r[7]),
            }
            for r in cur.fetchall()
        ]

    units_out = [
        {"device_id": i, "unit_name": UNITS[i].name, **quality.get(i, {"windows": 0, "avg_trust": None, "high": 0, "medium": 0, "low": 0})}
        for i in ids
    ]
    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "units": units_out,
        "alerts": alerts,
        "impact": impact,
        "total_revenue_at_risk": round(sum(r["revenue_at_risk"] for r in impact), 2),
    }


@router.get("/reports/summary")
def report_summary(start: datetime, end: datetime, device_id: list[str] | None = Query(None)):
    if end <= start:
        raise HTTPException(400, "end must be after start")
    with connect() as conn:
        return gather_report(conn, start, end, device_id)


def _table(rows: list[list]) -> Table:
    t = Table(rows, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f1b2d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f9")]),
            ]
        )
    )
    return t


@router.get("/reports/pdf")
def report_pdf(start: datetime, end: datetime, device_id: list[str] | None = Query(None)):
    if end <= start:
        raise HTTPException(400, "end must be after start")
    with connect() as conn:
        data = gather_report(conn, start, end, device_id)

    styles = getSampleStyleSheet()
    story = [
        Paragraph("ColdSense-DQ Cold-Chain Report", styles["Title"]),
        Paragraph(f"{data['range']['start']} to {data['range']['end']} (UTC)", styles["Normal"]),
        Spacer(1, 8 * mm),
        Paragraph("Data Quality Summary", styles["Heading2"]),
    ]
    q_rows = [["Unit", "Windows", "Avg trust", "High", "Medium", "Low"]]
    for u in data["units"]:
        q_rows.append([u["unit_name"], u["windows"], u["avg_trust"], u["high"], u["medium"], u["low"]])
    story += [_table(q_rows), Spacer(1, 6 * mm), Paragraph("Alerts", styles["Heading2"])]

    a_rows = [["Unit", "Type", "Severity", "Started (UTC)", "Minutes", "Peak C"]]
    for a in data["alerts"][:40]:
        a_rows.append([a["unit_name"], a["kind"], a["severity"], a["start_ts"][:16].replace("T", " "), a["minutes"], a["peak_temp_c"]])
    story.append(_table(a_rows) if len(a_rows) > 1 else Paragraph("No alerts in range.", styles["Normal"]))
    story += [Spacer(1, 6 * mm), Paragraph("Business Impact", styles["Heading2"])]

    i_rows = [["Unit", "Day", "Categories", "Spoilage prob.", "Sales exposed", "Revenue at risk"]]
    for r in data["impact"]:
        i_rows.append(
            [r["unit_name"], r["relative_day"], r["categories"], f"{r['spoilage_probability'] * 100:.0f}%", f"{r['sales_exposed']:.2f}", f"{r['revenue_at_risk']:.2f}"]
        )
    story.append(_table(i_rows) if len(i_rows) > 1 else Paragraph("No impact events in range.", styles["Normal"]))
    story += [Spacer(1, 4 * mm), Paragraph(f"<b>Total revenue at risk in range: {data['total_revenue_at_risk']:.2f}</b>", styles["Normal"])]

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm).build(story)
    return Response(content=buf.getvalue(), media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=coldsense-report.pdf"})
