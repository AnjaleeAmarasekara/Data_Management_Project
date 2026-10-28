"""Load the raw environmental sensor CSV into sensor_unit + sensor_reading.

Modeling decision (see docs/data_linkage.md): the dataset's 3 devices are mapped to
3 cold-storage units within a single modeled branch, since the source data carries
no branch/store field of its own.
"""

import sys
from pathlib import Path

import pandas as pd
import psycopg

from db import DATABASE_URL

RAW_CSV = Path(__file__).resolve().parent.parent / "data" / "raw" / "iot_telemetry_data.csv"

DEVICE_MAP = {
    "b8:27:eb:bf:9d:51": ("Freezer Unit 1", "freezer"),
    "00:0f:00:70:91:0a": ("Dairy Fridge Unit 2", "fridge"),
    "1c:bf:ce:15:ec:4d": ("Walk-in Cooler Unit 3", "cooler"),
}

READING_COLUMNS = [
    "device_id",
    "ts",
    "temp",
    "humidity",
    "co",
    "lpg",
    "smoke",
    "motion",
    "light",
]


def load_dataframe() -> pd.DataFrame:
    df = pd.read_csv(RAW_CSV)
    df["ts"] = pd.to_datetime(pd.to_numeric(df["ts"]), unit="s", utc=True)
    for bool_col in ("motion", "light"):
        df[bool_col] = df[bool_col].astype(str).str.strip().str.lower().map({"true": True, "false": False})
    df = df.rename(columns={"device": "device_id"})
    return df[READING_COLUMNS].sort_values("ts")


def upsert_sensor_units(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        for device_id, (unit_name, unit_type) in DEVICE_MAP.items():
            cur.execute(
                """
                INSERT INTO sensor_unit (device_id, unit_name, unit_type)
                VALUES (%s, %s, %s)
                ON CONFLICT (device_id) DO UPDATE
                    SET unit_name = EXCLUDED.unit_name, unit_type = EXCLUDED.unit_type
                """,
                (device_id, unit_name, unit_type),
            )
    conn.commit()


def bulk_load_readings(conn: psycopg.Connection, df: pd.DataFrame) -> None:
    col_list = ", ".join(READING_COLUMNS)
    total = len(df)
    with conn.cursor() as cur:
        with cur.copy(f"COPY sensor_reading ({col_list}) FROM STDIN") as copy:
            for i, row in enumerate(df.itertuples(index=False), start=1):
                copy.write_row(row)
                if i % 50_000 == 0:
                    print(f"  ...{i:,}/{total:,} rows staged")
    conn.commit()
    print(f"  done: {total:,} rows loaded")


def main() -> None:
    if not RAW_CSV.exists():
        sys.exit(f"Missing {RAW_CSV} -- download it first (see README).")

    print(f"Reading {RAW_CSV.name} ...")
    df = load_dataframe()
    print(f"Parsed {len(df):,} rows across {df['device_id'].nunique()} devices")
    print(f"Time range: {df['ts'].min()} to {df['ts'].max()}")

    with psycopg.connect(DATABASE_URL) as conn:
        print("Upserting sensor_unit ...")
        upsert_sensor_units(conn)
        print("Bulk-loading sensor_reading via COPY ...")
        bulk_load_readings(conn, df)


if __name__ == "__main__":
    main()
