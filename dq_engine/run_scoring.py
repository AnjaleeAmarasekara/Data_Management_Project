"""Batch-run the data quality framework over sensor_reading and persist results to
quality_score. Run after ingestion (Week 1) and before analytics (Week 3), matching the
proposal's layered architecture: raw data -> validation -> storage -> analytics.
"""

import sys
from pathlib import Path

import pandas as pd
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))

from db import DATABASE_URL  # noqa: E402
from dq_engine.config import DEFAULT_INTERVAL_S, EXPECTED_INTERVAL_S, WINDOW_MINUTES  # noqa: E402
from dq_engine.scoring import score_window  # noqa: E402



def fetch_readings(conn: psycopg.Connection, device_id: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ts, temp, humidity FROM sensor_reading WHERE device_id = %s ORDER BY ts",
            (device_id,),
        )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["ts", "temp", "humidity"])


def insert_score(conn: psycopg.Connection, score) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO quality_score
                (device_id, window_start, window_end, missing_flag, outlier_flag,
                 drift_flag, noise_flag, integrity_flag, trust_value, quality_label)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                score.device_id,
                score.window_start,
                score.window_end,
                score.missing_flag,
                score.outlier_flag,
                score.drift_flag,
                score.noise_flag,
                score.integrity_flag,
                score.trust_value,
                score.quality_label,
            ),
        )


def insert_flags(conn: psycopg.Connection, device_id: str, flagged: dict) -> None:
    rows = [(device_id, ts, reason) for reason, stamps in flagged.items() for ts in stamps]
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO reading_flag (device_id, ts, reason) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            rows,
        )


def run_for_device(conn: psycopg.Connection, device_id: str) -> int:
    expected_interval = EXPECTED_INTERVAL_S.get(device_id, DEFAULT_INTERVAL_S)
    df = fetch_readings(conn, device_id)
    if df.empty:
        return 0

    df["window"] = (df["ts"] - df["ts"].iloc[0]).dt.total_seconds() // (WINDOW_MINUTES * 60)
    count = 0
    for _, window_df in df.groupby("window"):
        if len(window_df) < 5:
            continue
        score = score_window(device_id, window_df.reset_index(drop=True), expected_interval)
        insert_score(conn, score)
        insert_flags(conn, device_id, score.flagged)
        count += 1
    conn.commit()
    return count


def main() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE quality_score, reading_flag")  # re-runs must not duplicate results
            cur.execute("SELECT device_id FROM sensor_unit")
            device_ids = [row[0] for row in cur.fetchall()]

        for device_id in device_ids:
            print(f"Scoring {device_id} ...")
            n = run_for_device(conn, device_id)
            print(f"  {n} windows scored")


if __name__ == "__main__":
    main()
