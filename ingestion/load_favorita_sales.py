"""Load retail perishable sales into sales_transaction.

Why this dataset (see docs/data_linkage.md): it carries real, named perishable product
families -- DAIRY, MEATS, POULTRY, SEAFOOD, PRODUCE, FROZEN FOODS, EGGS, DELI -- recorded
per store per day. Named families matter because they let each family be assigned to the
refrigerator zone that physically stores it (produce in the vegetable drawer, frozen goods
in the freezer compartment) instead of an arbitrary mapping.

One row here is a store-family-day total, not a single till receipt: that is the grain the
source publishes, and it is the grain the business-impact calculation needs.

Source: "Store Sales - Time Series Forecasting" (Corporacion Favorita, Ecuador), via
Kaggle. Store 32 is used as the demo premises: it is one of the smallest stores in the
set (type C), so its daily perishable volume is SME-scale rather than hypermarket-scale.

Currency: the source records units sold, not money. Values are converted to LKR with the
indicative retail prices in PRICES_LKR below so the business impact is expressed in the
currency a Sri Lankan SME actually works in. Prices marked "CBSL" are taken from the
Central Bank of Sri Lanka Daily Price Report of 23 October 2026; the rest are indicative
SME retail estimates and are labelled as such on the dashboard.
"""

import sys
from pathlib import Path

import pandas as pd
import psycopg

from db import DATABASE_URL

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "favorita"
TRAIN_CSV = RAW_DIR / "train.csv"

DEMO_STORE = 32
# The refrigerator record is anchored to this date in load_bangkok_sensors.py; sales are
# loaded for a wider window so the reports page has history either side of it.
DAY_ZERO = pd.Timestamp("2017-08-01").date()
DATE_FROM, DATE_TO = "2017-07-01", "2017-08-15"

# Favorita family -> (readable category, LKR per unit sold, price basis)
PRICES_LKR = {
    "PRODUCE": ("Produce", 400.0, "CBSL retail avg of beans/carrot/cabbage/tomato/brinjal/pumpkin"),
    "SEAFOOD": ("Seafood", 1400.0, "CBSL retail, mid-range of Kelawalla/Thalapath/Paraw/Katta"),
    "EGGS": ("Eggs", 30.0, "WFP Sri Lanka Market Monitor, approx. LKR 30 per egg"),
    "POULTRY": ("Poultry", 1100.0, "indicative SME retail estimate"),
    "MEATS": ("Meats", 1800.0, "indicative SME retail estimate"),
    "DAIRY": ("Dairy", 450.0, "indicative SME retail estimate"),
    "FROZEN FOODS": ("Frozen Foods", 900.0, "indicative SME retail estimate"),
    "DELI": ("Deli", 800.0, "indicative SME retail estimate"),
    "PREPARED FOODS": ("Prepared Foods", 500.0, "indicative SME retail estimate"),
    "BEVERAGES": ("Beverages", 350.0, "indicative SME retail estimate"),
}

COLUMNS = [
    "invoice_id",
    "sale_date",
    "relative_day",
    "customer_type",
    "gender",
    "product_category",
    "unit_price",
    "quantity",
    "total_sales",
    "payment_method",
]


def load_dataframe(store: int = DEMO_STORE) -> pd.DataFrame:
    df = pd.read_csv(TRAIN_CSV, usecols=["date", "store_nbr", "family", "sales"])
    df = df[(df["store_nbr"] == store) & df["family"].isin(PRICES_LKR) & df["date"].between(DATE_FROM, DATE_TO)]
    if df.empty:
        sys.exit(f"No rows for store {store} between {DATE_FROM} and {DATE_TO}.")

    df = df.groupby(["date", "family"], as_index=False)["sales"].sum()
    df["sale_date"] = pd.to_datetime(df["date"]).dt.date
    df["relative_day"] = df["sale_date"].apply(lambda d: (d - DAY_ZERO).days + 1)
    df["product_category"] = df["family"].map(lambda f: PRICES_LKR[f][0])
    df["unit_price"] = df["family"].map(lambda f: PRICES_LKR[f][1])
    df["quantity"] = df["sales"].round(3)
    df["total_sales"] = (df["sales"] * df["unit_price"]).round(2)
    df["invoice_id"] = [f"FAV{store}-{d}-{f.replace(' ', '')}" for d, f in zip(df["sale_date"], df["family"])]
    for nullable in ("customer_type", "gender", "payment_method"):
        df[nullable] = None
    return df[COLUMNS].sort_values(["sale_date", "product_category"])


def bulk_load(conn: psycopg.Connection, df: pd.DataFrame) -> None:
    col_list = ", ".join(COLUMNS)
    with conn.cursor() as cur:
        with cur.copy(f"COPY sales_transaction ({col_list}) FROM STDIN") as copy:
            for row in df.itertuples(index=False):
                copy.write_row(row)
    conn.commit()


def main() -> None:
    if not TRAIN_CSV.exists():
        sys.exit(f"Missing {TRAIN_CSV} -- see README for the download step.")

    print(f"Reading {TRAIN_CSV.name} (store {DEMO_STORE}, {DATE_FROM} to {DATE_TO}) ...")
    df = load_dataframe()
    print(f"Parsed {len(df):,} store-family-day rows across {df['product_category'].nunique()} categories")

    with psycopg.connect(DATABASE_URL) as conn:
        bulk_load(conn, df)

    print(f"Done: {len(df):,} rows, LKR {df['total_sales'].sum():,.0f} of perishable sales")


if __name__ == "__main__":
    main()
