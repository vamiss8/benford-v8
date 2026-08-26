#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Builds the demo CSV files: one honest set, one invented by a human.

The seed is fixed, so the files reproduce byte for byte:
    python samples/make_samples.py
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from benford import honest_amounts, cooked_amounts  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = 20260826
ROWS = 1200
LIMIT = 5000.0

VENDORS = ["Northwind Supplies", "Harbor Logistics", "Granite Works Ltd",
           "Beacon Print Co", "Kestrel Software", "Pinemark Services",
           "Atlas Freight", "Copperfield Repairs", "Larkspur Media",
           "Delta Facilities"]
CATEGORIES = ["stationery", "logistics", "repairs", "telecom", "rent",
              "training", "advertising", "software", "travel"]


def write(path: str, amounts: list[float], rng: random.Random) -> None:
    start = dt.date(2025, 1, 1)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["date", "vendor", "category", "amount"])
        for amount in amounts:
            day = start + dt.timedelta(days=rng.randrange(365))
            writer.writerow([day.isoformat(), rng.choice(VENDORS),
                             rng.choice(CATEGORIES), f"{amount:.2f}"])
    print(f"  {os.path.relpath(path)}  —  {len(amounts)} rows")


def main() -> None:
    rng = random.Random(SEED)
    print("building data sets:")
    write(os.path.join(HERE, "invoices_honest.csv"), honest_amounts(ROWS, rng), rng)
    write(os.path.join(HERE, "invoices_cooked.csv"),
          cooked_amounts(ROWS, rng, LIMIT), rng)
    print(f"\nspot the difference:\n"
          f"  python benford.py samples/invoices_honest.csv --limit {LIMIT:g}\n"
          f"  python benford.py samples/invoices_cooked.csv --limit {LIMIT:g}")


if __name__ == "__main__":
    main()
