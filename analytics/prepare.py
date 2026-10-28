"""Turn raw readings into the clean, minute-level series that analytics consumes.

This is where the proposal's central argument is enforced in code: analytics never sees
raw readings. Readings flagged by the data quality layer (outliers, noise) are dropped
first, then each remaining minute carries the trust score of the window it belongs to.
"""

import pandas as pd
import psycopg

from analytics.config import UnitConfig

DROP_REASONS = ("outlier", "noise", "integrity")


def load_flagged_timestamps(conn: psycopg.Connection, device_id: str) -> set:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ts FROM reading_flag WHERE device_id = %s AND reason = ANY(%s)",
            (device_id, list(DROP_REASONS)),
        )
        return {row[0] for row in cur.fetchall()}


def load_trust_windows(conn: psycopg.Connection, device_id: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT window_start, trust_value FROM quality_score WHERE device_id = %s ORDER BY window_start",
            (device_id,),
        )
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["window_start", "trust"])
    df["trust"] = df["trust"].astype(float)
    return df


def load_clean_minutely(conn: psycopg.Connection, device_id: str, cfg: UnitConfig, day_zero: pd.Timestamp) -> pd.DataFrame:
    """Minute-mean temp/humidity with flagged readings removed, mapped onto the unit's
    setpoint, tagged with window trust and a relative_day index."""
    with conn.cursor() as cur:
        cur.execute("SELECT ts, temp, humidity FROM sensor_reading WHERE device_id = %s ORDER BY ts", (device_id,))
        raw = pd.DataFrame(cur.fetchall(), columns=["ts", "temp", "humidity"])

    flagged = load_flagged_timestamps(conn, device_id)
    clean = raw[~raw["ts"].isin(flagged)].dropna(subset=["temp"])

    minutely = (
        clean.set_index("ts")[["temp", "humidity"]]
        .resample("1min")
        .mean()
        .dropna(subset=["temp"])
        .reset_index()
    )

    baseline = clean["temp"].median()
    minutely["cold_temp"] = cfg.to_cold(minutely["temp"], baseline)

    trust = load_trust_windows(conn, device_id)
    minutely = pd.merge_asof(minutely.sort_values("ts"), trust, left_on="ts", right_on="window_start", direction="backward")
    minutely["trust"] = minutely["trust"].fillna(0.0)
    minutely["relative_day"] = (minutely["ts"].dt.floor("D") - day_zero).dt.days + 1
    return minutely.drop(columns=["window_start"])


def sensor_day_zero(conn: psycopg.Connection) -> pd.Timestamp:
    with conn.cursor() as cur:
        cur.execute("SELECT min(ts) FROM sensor_reading")
        return pd.Timestamp(cur.fetchone()[0]).floor("D")
