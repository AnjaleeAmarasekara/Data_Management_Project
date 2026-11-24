"""Load the Bangkok household-refrigerator dataset into sensor_unit + sensor_reading.

Why this dataset (see docs/data_linkage.md): it is real refrigeration data -- freezer
compartments near -20C, chilled shelves at 1-7C -- recorded in a tropical climate with
ambient air at 22-33C, which is the condition a Sri Lankan SME's cold equipment actually
works in. The earlier development dataset was room-level environmental telemetry, which
forced the analytics layer to simulate fridge temperatures; that simulation is now gone.

Modeling decision: each instrumented refrigerator is modeled as one SME premises ("site"),
and each of its five monitored zones is modeled as one cold-storage unit. A small shop
really does run its frozen, dairy, produce and beverage stock in separate zones of one or
two cabinets, so the zone -> unit mapping is physical rather than arbitrary.

Source: Paviet-Salomon, Y. et al., "Dataset of household cold-chain conditions:
temperature and relative humidity monitoring in domestic refrigerators in Bangkok,
Thailand", Recherche Data Gouv (INRAE), https://doi.org/10.57745/TMWYBQ, CC BY-NC-SA 4.0.
Logger: TagPlus-TH, 0.1C resolution, +/-0.5C expanded uncertainty, 5-minute interval.
"""

import sys
from pathlib import Path

import pandas as pd
import psycopg

from db import DATABASE_URL

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "bangkok_fridges" / "01_src"

# The recorded window carries no calendar dates (the source column is elapsed minutes), so
# it is anchored to the retail week used by the sales dataset. This alignment is a modeling
# choice and is stated as such on the dashboard and in docs/data_linkage.md.
DAY_ZERO = pd.Timestamp("2017-08-01T00:00:00Z")

# Zone code -> (readable unit name, unit_type, temperature column, humidity column).
# Zone codes come from the dataset README: FC freezer compartment, TS top shelf,
# MS middle shelf, VD vegetable drawer, DS door shelf.
#
# PT and AM are reference channels rather than cold-storage units:
#
#   PT  the sensor embedded in a meat-simulating gel. This is the *product* temperature,
#       which is what actually determines spoilage - air temperature is only a proxy for
#       it. The source README does not say which shelf the gel sits on, so its position
#       was inferred from the data: across all 123 refrigerators TP correlates 0.72 with
#       the middle shelf and sits 0.67 C from it on average, far closer than any other
#       zone (next best: top shelf, 0.43 and 3.02 C). It is therefore treated as the
#       middle shelf's product probe. The gel carries no humidity sensor.
#
#   AM  ambient air outside the refrigerator. It distinguishes "this unit is failing"
#       from "the shop is 33 C today", which matters for a tropical SME.
ZONES = {
    "FC": ("Freezer Compartment", "freezer", "T_FC", "RH_FC"),
    "TS": ("Top Shelf Chiller", "chiller", "T_TS", "RH_TS"),
    "MS": ("Middle Shelf Chiller", "chiller", "T_MS", "RH_MS"),
    "VD": ("Vegetable Drawer", "crisper", "T_VD", "RH_VD"),
    "DS": ("Door Shelf", "door", "T_DS", "RH_DS"),
    "PT": ("Product Probe (middle shelf)", "product_probe", "TP", None),
    "AM": ("Ambient Air", "ambient", "T_AMB", "RH_AMB"),
}

# Channels that measure something other than a cold-storage unit's air. They are stored
# and quality-scored like any other channel, but they carry no safe limit and raise no
# cold-chain excursion alerts.
REFERENCE_ZONES = {"PT", "AM"}

FRIDGE_TYPE_LABEL = {
    "1D": "single-door",
    "2DT": "two-door, top freezer",
    "2DB": "two-door, bottom freezer",
}

READING_COLUMNS = ["device_id", "ts", "temp", "humidity"]


def site_id_for(shop: str) -> str:
    return f"BKK-{shop}"


def device_id_for(shop: str, zone: str) -> str:
    return f"BKK-{shop}-{zone}"


def source_files() -> list[Path]:
    return sorted(p for p in RAW_DIR.glob("*.txt") if p.stem[0].isdigit())


def parse_file(path: Path) -> tuple[dict, pd.DataFrame]:
    """One source file -> (site metadata, long-format readings for its five zones)."""
    shop, fridge_type = path.stem.split("_", 1)
    raw = pd.read_csv(path, sep="\t")
    ts = DAY_ZERO + pd.to_timedelta(raw["Time"], unit="m")

    frames = []
    for zone, (_, _, temp_col, hum_col) in ZONES.items():
        frames.append(
            pd.DataFrame(
                {
                    "device_id": device_id_for(shop, zone),
                    "ts": ts,
                    "temp": raw[temp_col].astype(float),
                    # the product gel has no humidity sensor
                    "humidity": raw[hum_col].astype(float) if hum_col else None,
                }
            )
        )

    site = {
        "shop": shop,
        "fridge_type": fridge_type,
        "site_id": site_id_for(shop),
        "site_name": f"Shop {shop} ({FRIDGE_TYPE_LABEL.get(fridge_type, fridge_type)})",
    }
    return site, pd.concat(frames, ignore_index=True)


def upsert_units(conn: psycopg.Connection, sites: list[dict]) -> None:
    with conn.cursor() as cur:
        for site in sites:
            for zone, (unit_name, unit_type, _, _) in ZONES.items():
                cur.execute(
                    """
                    INSERT INTO sensor_unit (device_id, unit_name, unit_type, branch_name, site_id, site_name, zone)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (device_id) DO UPDATE
                        SET unit_name = EXCLUDED.unit_name,
                            unit_type = EXCLUDED.unit_type,
                            branch_name = EXCLUDED.branch_name,
                            site_id = EXCLUDED.site_id,
                            site_name = EXCLUDED.site_name,
                            zone = EXCLUDED.zone
                    """,
                    (
                        device_id_for(site["shop"], zone),
                        unit_name,
                        unit_type,
                        site["site_name"],
                        site["site_id"],
                        site["site_name"],
                        zone,
                    ),
                )
    conn.commit()


def bulk_load_readings(conn: psycopg.Connection, df: pd.DataFrame) -> None:
    col_list = ", ".join(READING_COLUMNS)
    with conn.cursor() as cur:
        with cur.copy(f"COPY sensor_reading ({col_list}) FROM STDIN") as copy:
            for row in df.itertuples(index=False):
                copy.write_row(row)
    conn.commit()


def main() -> None:
    files = source_files()
    if not files:
        sys.exit(f"No refrigerator files under {RAW_DIR} -- see README for the download step.")

    print(f"Found {len(files)} instrumented refrigerators in {RAW_DIR.name}/")

    sites, total = [], 0
    with psycopg.connect(DATABASE_URL) as conn:
        parsed = [parse_file(p) for p in files]
        sites = [s for s, _ in parsed]
        upsert_units(conn, sites)
        print(f"Registered {len(sites)} sites x {len(ZONES)} zones = {len(sites) * len(ZONES)} units")

        for i, (site, readings) in enumerate(parsed, start=1):
            bulk_load_readings(conn, readings)
            total += len(readings)
            if i % 25 == 0 or i == len(parsed):
                print(f"  ...{i}/{len(parsed)} sites loaded ({total:,} readings)")

    span_end = DAY_ZERO + pd.Timedelta(minutes=7340)
    print(f"Done: {total:,} readings, {DAY_ZERO.date()} to {span_end.date()}")


if __name__ == "__main__":
    main()
