"""Batch-run the data quality framework over sensor_reading and persist results to
quality_score. Run after ingestion (Week 1) and before analytics (Week 3), matching the
proposal's layered architecture: raw data -> validation -> storage -> analytics.
"""

import sys
import time
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


SCORE_SQL = """
    INSERT INTO quality_score
        (device_id, window_start, window_end, missing_flag, outlier_flag,
         drift_flag, noise_flag, integrity_flag, trust_value, quality_label)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
FLAG_SQL = "INSERT INTO reading_flag (device_id, ts, reason) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING"


def observation_bounds(conn: psycopg.Connection) -> dict[str, tuple]:
    """For every device, the period it *should* have reported over.

    The grid is set by the premises, not by the device itself: a gateway uploads every
    zone of a fridge together, so if one zone falls silent while its siblings carry on,
    the silence is measured against the siblings. Anchoring each device to its own first
    and last reading would hide exactly that - a sensor that died early would simply have
    a shorter record, not a gap.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.device_id, b.first_ts, b.last_ts
            FROM sensor_unit u
            JOIN (
                SELECT u2.site_id, min(r.ts) AS first_ts, max(r.ts) AS last_ts
                FROM sensor_reading r JOIN sensor_unit u2 USING (device_id)
                GROUP BY u2.site_id
            ) b ON b.site_id = u.site_id
            """
        )
        return {d: (first, last) for d, first, last in cur.fetchall()}


def window_grid(first_ts, last_ts) -> list[tuple]:
    """Fixed windows from the start of observation to the end. Every slot is scored, so an
    outage shows up as missing windows instead of disappearing from the record."""
    step = pd.Timedelta(minutes=WINDOW_MINUTES)
    grid, start = [], first_ts
    while start <= last_ts:
        grid.append((start, min(start + step, last_ts)))
        start += step
    return grid


def run_for_device(conn: psycopg.Connection, device_id: str, bounds: tuple | None = None) -> int:
    """Score every window for one device and write the results in one batch.

    Windows come from a fixed grid over the premises' observation period. They used to be
    formed from whatever readings existed, so a window in which the sensor sent nothing
    produced no row at all: measured on real data, a 3-hour and a 6-hour outage both left
    no missing-data flag anywhere.

    All of a device's windows are scored in memory and written with two batched statements,
    inside one transaction - so a device is either fully scored or not at all, which is what
    makes --resume safe. Batching removed one database round trip per window, but measured
    on one channel that was the minor cost: fetching took 76 ms and scoring 926 ms (about
    15 ms per window). The statistics dominate, which is why the real speed-up is running
    devices in parallel (--workers), not trimming the writes further.
    """
    expected_interval = EXPECTED_INTERVAL_S.get(device_id, DEFAULT_INTERVAL_S)
    df = fetch_readings(conn, device_id)
    if bounds is None:
        if df.empty:
            return 0
        bounds = (df["ts"].iloc[0], df["ts"].iloc[-1])

    grid = window_grid(*bounds)
    score_rows, flag_rows = [], []
    for i, (w_start, w_end) in enumerate(grid):
        last = i == len(grid) - 1
        in_window = (df["ts"] >= w_start) & ((df["ts"] <= w_end) if last else (df["ts"] < w_end))
        window_df = df[in_window].reset_index(drop=True)
        sc = score_window(device_id, window_df, expected_interval, window_start=w_start, window_end=w_end)
        score_rows.append((
            sc.device_id, sc.window_start, sc.window_end, sc.missing_flag, sc.outlier_flag,
            sc.drift_flag, sc.noise_flag, sc.integrity_flag, sc.trust_value, sc.quality_label,
        ))
        flag_rows.extend((device_id, ts, reason) for reason, stamps in sc.flagged.items() for ts in stamps)

    with conn.cursor() as cur:
        if score_rows:
            cur.executemany(SCORE_SQL, score_rows)
        if flag_rows:
            cur.executemany(FLAG_SQL, flag_rows)
    conn.commit()
    return len(score_rows)


def _score_chunk(job: tuple) -> int:
    """Worker process: its own database connection, a slice of the devices."""
    device_ids, bounds = job
    total = 0
    with psycopg.connect(DATABASE_URL) as conn:
        for device_id in device_ids:
            total += run_for_device(conn, device_id, bounds.get(device_id))
    return total


def _score_parallel(device_ids: list[str], workers: int, bounds: dict) -> int:
    """Devices are independent, and scoring is CPU-bound statistics, so the fleet splits
    cleanly across processes. Round-robin slicing spreads the different refrigerator types
    evenly instead of giving one worker all the slow ones."""
    from concurrent.futures import ProcessPoolExecutor

    jobs = [(device_ids[i::workers], bounds) for i in range(workers)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return sum(pool.map(_score_chunk, jobs))


def main() -> None:
    # Optional site id (e.g. BKK-012) rescores just that premises, which keeps the
    # edit-and-check loop short while tuning a threshold. With no argument every
    # instrumented refrigerator is rescored from scratch.
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    resume = "--resume" in sys.argv
    workers = next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--workers=")), 1)
    only_site = args[0] if args else None

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            if resume:
                # Each device is written in a single transaction, so any device with rows is
                # complete. Skip those and score only what an interrupted run never reached,
                # instead of truncating and starting the whole fleet again.
                cur.execute(
                    """
                    SELECT device_id FROM sensor_unit u
                    WHERE NOT EXISTS (SELECT 1 FROM quality_score q WHERE q.device_id = u.device_id)
                    """
                    + (" AND u.site_id = %s" if only_site else ""),
                    (only_site,) if only_site else None,
                )
            elif only_site:
                cur.execute(
                    "DELETE FROM reading_flag WHERE device_id IN (SELECT device_id FROM sensor_unit WHERE site_id = %s)",
                    (only_site,),
                )
                cur.execute(
                    "DELETE FROM quality_score WHERE device_id IN (SELECT device_id FROM sensor_unit WHERE site_id = %s)",
                    (only_site,),
                )
                cur.execute("SELECT device_id FROM sensor_unit WHERE site_id = %s", (only_site,))
            else:
                cur.execute("TRUNCATE quality_score, reading_flag")  # re-runs must not duplicate results
                cur.execute("SELECT device_id FROM sensor_unit")
            device_ids = [row[0] for row in cur.fetchall()]
        conn.commit()

        if not device_ids:
            if resume:
                print("Nothing to resume: every unit already has scores.")
                return
            sys.exit(f"No units found{f' for site {only_site}' if only_site else ''}.")

        bounds = observation_bounds(conn)

        if workers > 1 and len(device_ids) > workers:
            started = time.perf_counter()
            total = _score_parallel(device_ids, workers, bounds)
            elapsed = time.perf_counter() - started
            print(f"Done: {total:,} windows across {len(device_ids)} units "
                  f"in {elapsed:.0f}s with {workers} workers")
            return

        total = 0
        for i, device_id in enumerate(device_ids, start=1):
            n = run_for_device(conn, device_id, bounds.get(device_id))
            total += n
            if len(device_ids) <= 10:
                print(f"  {device_id}: {n} windows scored")
            elif i % 100 == 0:
                print(f"  ...{i}/{len(device_ids)} units ({total:,} windows)", flush=True)
        print(f"Done: {total:,} windows across {len(device_ids)} units")


if __name__ == "__main__":
    main()
