#!/usr/bin/env python3
"""Extract one customer-year from the Ausgrid 'Solar home electricity data'
into a clean timeseries CSV for battery-worth.

The Ausgrid format is wide: one row per (customer, day, channel), with 48
half-hourly columns. Channels:
    GG = gross generation (PV production)
    GC = general consumption
    CL = controlled load (off-peak, present for some customers only)

Two conventions matter:
  * The half-hourly columns are INTERVAL-ENDING ("0:30" = the half hour that
    ends at 00:30, i.e. starts at 00:00).
  * The "0:00" column therefore belongs to the NEXT day.
Both are handled below.

Output: timestamp (naive local time), consumption, pv_production — in kWh per
half hour. Feed it to battery-worth with schema B and timezone
"Australia/Sydney".

Usage:
    python extract_ausgrid_customer.py --inspect FILE.csv
    python extract_ausgrid_customer.py FILE.csv --customer 1 -o customer_1.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

HALF_HOURS_PER_DAY = 48


def read_ausgrid(path: Path) -> pd.DataFrame:
    """Read the raw file, skipping the preamble line if present."""
    df = pd.read_csv(path)
    if "Customer" not in df.columns:  # some releases have a notes line on top
        df = pd.read_csv(path, skiprows=1)
    if "Customer" not in df.columns:
        msg = f"Unexpected header. Columns found: {list(df.columns)[:8]}"
        raise SystemExit(msg)
    return df


def half_hour_columns(df: pd.DataFrame) -> list[str]:
    """Return the 48 time columns, in file order."""
    cols = [c for c in df.columns if ":" in str(c)]
    if len(cols) != HALF_HOURS_PER_DAY:
        msg = f"Expected {HALF_HOURS_PER_DAY} half-hourly columns, found {len(cols)}: {cols[:5]}..."
        raise SystemExit(msg)
    return cols


def reshape(df: pd.DataFrame, customer: int) -> pd.DataFrame:
    sub = df[df["Customer"] == customer].copy()
    if sub.empty:
        available = sorted(df["Customer"].unique())[:10]
        msg = f"Customer {customer} not found. First available: {available}"
        raise SystemExit(msg)

    date_col = next(c for c in sub.columns if c.strip().lower() == "date")
    cat_col = next(c for c in sub.columns if "category" in c.strip().lower())
    time_cols = half_hour_columns(sub)

    sub[date_col] = pd.to_datetime(sub[date_col], dayfirst=True)

    long = sub.melt(
        id_vars=[date_col, cat_col],
        value_vars=time_cols,
        var_name="slot",
        value_name="kwh",
    )
    long["kwh"] = pd.to_numeric(long["kwh"], errors="coerce").fillna(0.0)

    # Interval-ENDING -> interval-STARTING: subtract 30 minutes.
    # This also moves the "0:00" column to 23:30 of the same day, which is
    # correct: it is the last half hour of that day.
    end = pd.to_datetime(
        long[date_col].dt.strftime("%Y-%m-%d") + " " + long["slot"].str.strip(),
        format="%Y-%m-%d %H:%M",
    )
    # "0:00" parses as 00:00 of the same day but means end-of-day -> add 1 day
    end = end.where(long["slot"].str.strip() != "0:00", end + pd.Timedelta(days=1))
    long["timestamp"] = end - pd.Timedelta(minutes=30)

    wide = long.pivot_table(
        index="timestamp", columns=cat_col, values="kwh", aggfunc="sum"
    ).sort_index()

    if "GG" not in wide.columns:
        msg = f"No GG (PV) channel for this customer. Channels: {list(wide.columns)}"
        raise SystemExit(msg)

    out = pd.DataFrame(index=wide.index)
    out["pv_production"] = wide["GG"]
    consumption = wide.get("GC", 0.0)
    if "CL" in wide.columns:  # controlled load is real household consumption
        consumption = consumption + wide["CL"].fillna(0.0)
    out["consumption"] = consumption
    return out.round(4)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("file", type=Path)
    p.add_argument("--customer", type=int, default=1)
    p.add_argument("-o", "--output", type=Path, default=Path("ausgrid_customer.csv"))
    p.add_argument("--inspect", action="store_true", help="Show header and exit")
    args = p.parse_args()

    df = read_ausgrid(args.file)

    if args.inspect:
        print("Columns:", list(df.columns)[:8], "...")
        cat_col = next(c for c in df.columns if "category" in c.strip().lower())
        print("Channels:", sorted(df[cat_col].unique()))
        print("Customers:", df["Customer"].min(), "->", df["Customer"].max())
        print(df.head(3).iloc[:, :8].to_string())
        return

    out = reshape(df, args.customer)
    out.to_csv(args.output, index_label="timestamp")

    days = (out.index.max() - out.index.min()).days + 1
    print(f"Written {args.output}  ({len(out)} rows, {days} days)")
    print(f"  period      : {out.index.min()} -> {out.index.max()}")
    print(f"  PV total    : {out['pv_production'].sum():.0f} kWh")
    print(f"  consumption : {out['consumption'].sum():.0f} kWh")
    gaps = pd.date_range(out.index.min(), out.index.max(), freq="30min").difference(out.index)  # type: ignore
    print(f"  missing slots: {len(gaps)}")


if __name__ == "__main__":
    main()
