"""Load the raw supermarket sales CSV into sales_transaction.

Modeling decision (see docs/data_linkage.md): dates are re-indexed to a
`relative_day` (1-based offset from this dataset's own minimum date) because the
sales dataset's real calendar dates don't overlap with the sensor dataset's. Business
impact analysis joins the two datasets on relative_day, not on calendar date.
"""

import sys
from pathlib import Path

import pandas as pd
import psycopg

from db import DATABASE_URL

RAW_CSV = Path(__file__).resolve().parent.parent / "data" / "raw" / "supermarket_sales_150 - Sheet1.csv"

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


def load_dataframe() -> pd.DataFrame:
    df = pd.read_csv(RAW_CSV)
    df = df.rename(
        columns={
            "Invoice ID": "invoice_id",
            "Date": "sale_date",
            "Customer Type": "customer_type",
            "Gender": "gender",
            "Product Category": "product_category",
            "Unit Price": "unit_price",
            "Quantity": "quantity",
            "Total Sales": "total_sales",
            "Payment Method": "payment_method",
        }
    )
    df["sale_date"] = pd.to_datetime(df["sale_date"]).dt.date
    min_date = df["sale_date"].min()
    df["relative_day"] = df["sale_date"].apply(lambda d: (d - min_date).days + 1)
    df["gender"] = df["gender"].where(df["gender"].notna(), None)
    return df[COLUMNS]


def bulk_load(conn: psycopg.Connection, df: pd.DataFrame) -> None:
    col_list = ", ".join(COLUMNS)
    with conn.cursor() as cur:
        with cur.copy(f"COPY sales_transaction ({col_list}) FROM STDIN") as copy:
            for row in df.itertuples(index=False):
                copy.write_row(row)
    conn.commit()
    print(f"  done: {len(df):,} rows loaded")


def main() -> None:
    if not RAW_CSV.exists():
        sys.exit(f"Missing {RAW_CSV} -- download it first (see README).")

    print(f"Reading {RAW_CSV.name} ...")
    df = load_dataframe()
    print(f"Parsed {len(df):,} rows, relative_day range {df['relative_day'].min()}-{df['relative_day'].max()}")

    with psycopg.connect(DATABASE_URL) as conn:
        print("Bulk-loading sales_transaction via COPY ...")
        bulk_load(conn, df)


if __name__ == "__main__":
    main()
