import os
from functools import lru_cache

import psycopg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://coldsense:coldsense@localhost:5432/coldsense_dq")


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


@lru_cache(maxsize=None)
def baseline_temp(device_id: str) -> float:
    """Median of a unit's clean readings -- the same baseline analytics uses to map raw
    temperature onto the unit's setpoint. Cached: it only changes when data is reloaded."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY temp)
            FROM sensor_reading r
            WHERE device_id = %s AND NOT EXISTS (
                SELECT 1 FROM reading_flag f
                WHERE f.device_id = r.device_id AND f.ts = r.ts AND f.reason IN ('outlier', 'noise'))
            """,
            (device_id,),
        )
        return float(cur.fetchone()[0])
