#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import datetime as dt
from pathlib import Path
from typing import Dict, List, Set, Tuple

import baostock as bs
import pandas as pd
from tqdm import tqdm

def normalize_code(code: str) -> str:
    code = code.strip().upper()
    if code.startswith(("6", "5", "9")):
        return f"SH{code}"
    return f"SZ{code}"

def load_qlib_instruments(path: Path) -> List[Tuple[str, dt.date, dt.date]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) == 3:
                code, start, end = parts
                rows.append((code, dt.datetime.strptime(start, "%Y-%m-%d").date(), dt.datetime.strptime(end, "%Y-%m-%d").date()))
    return rows

def get_constituents_from_qlib(rows: List[Tuple[str, dt.date, dt.date]], date: dt.date) -> Set[str]:
    active = set()
    for code, start, end in rows:
        if start <= date <= end:
            active.add(code)
    return active

def get_constituents_from_baostock(date: dt.date) -> Set[str]:
    date_str = date.strftime("%Y-%m-%d")
    rs = bs.query_hs300_stocks(date=date_str)
    if rs.error_code != "0":
        # Maybe it's a non-trading day? Baostock query_hs300_stocks usually returns the list for the last trading day
        # but let's be safe.
        return set()
    
    symbols = []
    while rs.next():
        # row[1] is code, e.g. "sh.600000"
        raw_code = rs.get_row_data()[1].upper()
        # convert "SH.600000" to "SH600000"
        clean_code = raw_code.replace(".", "")
        symbols.append(clean_code)
    return set(symbols)

def login():
    lg = bs.login()
    if lg.error_code != '0':
        raise RuntimeError(f"Baostock login failed: {lg.error_msg}")

def main():
    parser = argparse.ArgumentParser(description="Verify CSI300 historical constituents against Baostock.")
    parser.add_argument("--file", type=Path, required=True, help="Path to qlib instruments/csi300.txt")
    parser.add_argument("--samples", type=int, default=10, help="Number of dates to sample")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"Error: File not found: {args.file}")
        return

    print(f"Loading local file: {args.file}")
    qlib_rows = load_qlib_instruments(args.file)
    
    # Get the date range from the file
    all_starts = [r[1] for r in qlib_rows]
    all_ends = [r[2] for r in qlib_rows if r[2].year < 2099] # Ignore sentinel 2099
    
    min_date = min(all_starts)
    max_date = max(all_ends) if all_ends else dt.date.today()
    
    print(f"File covers range: {min_date} to {max_date}")
    
    # Sample dates
    delta = (max_date - min_date).days
    sample_dates = []
    for i in range(args.samples):
        d = min_date + dt.timedelta(days=int(delta * i / (args.samples - 1)))
        sample_dates.append(d)
    
    # Also add the very last date in the file (most recent one before 2099 or today)
    if max_date not in sample_dates:
        sample_dates.append(max_date)

    login()
    try:
        results = []
        for date in tqdm(sample_dates, desc="Verifying dates"):
            bs_stocks = get_constituents_from_baostock(date)
            if not bs_stocks:
                # Try finding the previous trading day if it's a weekend/holiday
                # (Though Baostock's query_hs300_stocks usually handles this by returning the most recent list)
                continue
                
            qlib_stocks = get_constituents_from_qlib(qlib_rows, date)
            
            intersection = bs_stocks.intersection(qlib_stocks)
            diff_bs = bs_stocks - qlib_stocks # In BS but not in Qlib
            diff_qlib = qlib_stocks - bs_stocks # In Qlib but not in BS
            
            results.append({
                "date": date,
                "overlap": len(intersection),
                "bs_only": sorted(list(diff_bs)),
                "qlib_only": sorted(list(diff_qlib)),
                "bs_total": len(bs_stocks),
                "qlib_total": len(qlib_stocks)
            })

        print("\nVerification Results:")
        print("-" * 100)
        print(f"{'Date':<12} | {'Overlap':<7} | {'BS Total':<8} | {'Qlib Total':<10} | {'Missing in Qlib':<20} | {'Extra in Qlib'}")
        print("-" * 100)
        for r in results:
            missing = ",".join(r["bs_only"][:3]) + ("..." if len(r["bs_only"]) > 3 else "")
            extra = ",".join(r["qlib_only"][:3]) + ("..." if len(r["qlib_only"]) > 3 else "")
            print(f"{str(r['date']):<12} | {r['overlap']:<7} | {r['bs_total']:<8} | {r['qlib_total']:<10} | {missing:<20} | {extra}")
        
    finally:
        bs.logout()

if __name__ == "__main__":
    main()
