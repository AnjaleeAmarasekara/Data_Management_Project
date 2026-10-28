"""Batch-run analytics over quality-filtered data and persist events + business impact.

Order matters (proposal architecture): ingestion -> dq_engine/run_scoring.py -> this script.
"""

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ingestion"))

from analytics.anomaly import detect_excursions, detect_isolation_forest  # noqa: E402
from analytics.business_impact import estimate_impact  # noqa: E402
from analytics.config import UNITS  # noqa: E402
from analytics.prepare import load_clean_minutely, sensor_day_zero  # noqa: E402
from db import DATABASE_URL  # noqa: E402


def load_sales_by_day(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT relative_day, product_category, sum(total_sales) FROM sales_transaction GROUP BY 1, 2")
        out: dict = {}
        for day, category, total in cur.fetchall():
            out.setdefault(day, {})[category] = float(total)
        return out


def store_events(conn: psycopg.Connection, device_id: str, events) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO anomaly_event
                (device_id, kind, start_ts, end_ts, minutes, peak_temp_c, severity, avg_trust)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (device_id, e.kind, e.start_ts, e.end_ts, e.minutes, e.peak_temp_c, e.severity, e.avg_trust)
                for e in events
            ],
        )


def store_impact(conn: psycopg.Connection, device_id: str, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO impact_estimate
                (device_id, relative_day, categories, sales_exposed, excursion_minutes, degree_minutes,
                 peak_temp_c, spoilage_probability, revenue_at_risk, avg_trust)
            VALUES (%(device_id)s, %(relative_day)s, %(categories)s, %(sales_exposed)s, %(excursion_minutes)s,
                    %(degree_minutes)s, %(peak_temp_c)s, %(spoilage_probability)s, %(revenue_at_risk)s, %(avg_trust)s)
            """,
            [{**r, "device_id": device_id} for r in rows],
        )


def main() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # alert_acknowledgement FKs into anomaly_event, and a re-run regenerates
            # anomaly_event with new ids anyway, so any prior acknowledgement is reset.
            cur.execute("TRUNCATE alert_acknowledgement, anomaly_event, impact_estimate")

        day_zero = sensor_day_zero(conn)
        sales_by_day = load_sales_by_day(conn)

        for device_id, cfg in UNITS.items():
            df = load_clean_minutely(conn, device_id, cfg, day_zero)
            excursions = detect_excursions(df, cfg.max_safe_c)
            watch = detect_isolation_forest(df, cfg.max_safe_c)
            impact = estimate_impact(df, cfg, sales_by_day)

            store_events(conn, device_id, excursions + watch)
            store_impact(conn, device_id, impact)
            total_risk = sum(r["revenue_at_risk"] for r in impact)
            print(
                f"{cfg.name}: {len(df):,} clean minutes | {len(excursions)} excursions, "
                f"{len(watch)} watch events | revenue at risk {total_risk:,.2f}"
            )
        conn.commit()


if __name__ == "__main__":
    main()
