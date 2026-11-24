"""Batch-run analytics over quality-filtered data and persist events + business impact.

Order matters (proposal architecture): ingestion -> dq_engine/run_scoring.py -> this script.
"""

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ingestion"))

from analytics.anomaly import (  # noqa: E402
    detect_cold_excursions,
    detect_excursions,
    detect_isolation_forest,
    detect_silences,
)
from analytics.business_impact import estimate_impact  # noqa: E402
from analytics.config import PRODUCT_PROBE_FOR, UNITS  # noqa: E402
from analytics.prepare import load_clean_minutely, sensor_day_zero  # noqa: E402
from dq_engine.config import SILENCE_THRESHOLD_MIN  # noqa: E402
from dq_engine.run_scoring import fetch_readings, observation_bounds  # noqa: E402
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
                 peak_temp_c, spoilage_probability, revenue_at_risk, avg_trust, temp_basis,
                 cold_minutes, cold_degree_minutes, low_temp_c)
            VALUES (%(device_id)s, %(relative_day)s, %(categories)s, %(sales_exposed)s, %(excursion_minutes)s,
                    %(degree_minutes)s, %(peak_temp_c)s, %(spoilage_probability)s, %(revenue_at_risk)s, %(avg_trust)s,
                    %(temp_basis)s, %(cold_minutes)s, %(cold_degree_minutes)s, %(low_temp_c)s)
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

        # A probe inside the goods is loaded once per unit that has one, so the impact model
        # can work from product temperature instead of the air around it.
        probes = {
            unit: load_clean_minutely(conn, probe_id, UNITS[unit], day_zero)
            for unit, probe_id in PRODUCT_PROBE_FOR.items()
            if unit in UNITS
        }

        bounds = observation_bounds(conn)

        for device_id, cfg in UNITS.items():
            df = load_clean_minutely(conn, device_id, cfg, day_zero)
            excursions = detect_excursions(df, cfg.max_safe_c)
            cold = detect_cold_excursions(df, cfg.min_safe_c)
            watch = detect_isolation_forest(df, cfg.max_safe_c, cfg.min_safe_c)
            silent = detect_silences(fetch_readings(conn, device_id), bounds[device_id][1], SILENCE_THRESHOLD_MIN)
            impact = estimate_impact(df, cfg, sales_by_day, product_df=probes.get(device_id))

            store_events(conn, device_id, excursions + cold + watch + silent)
            store_impact(conn, device_id, impact)
            total_risk = sum(r["revenue_at_risk"] for r in impact)
            basis = impact[0]["temp_basis"] if impact else "-"
            print(
                f"{cfg.name}: {len(df):,} clean samples | {len(excursions)} warm, {len(cold)} cold, "
                f"{len(watch)} watch, {len(silent)} silent | risk LKR {total_risk:,.2f} (from {basis} temperature)"
            )
        conn.commit()


if __name__ == "__main__":
    main()
