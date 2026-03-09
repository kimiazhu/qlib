#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import argparse
import pickle
import time
from pathlib import Path
from typing import Dict, List, Set, Tuple

import baostock as bs
import pandas as pd
from tqdm import tqdm


DEFAULT_START_DATE = "2005-01-01"
DEFAULT_END_DATE = pd.Timestamp.today().strftime("%Y-%m-%d")
DEFAULT_FAR_END_DATE = "2099-12-31"
DEFAULT_CACHE_FILE_NAME = "csi300_baostock_daily_cache.pkl"


def normalize_symbol(symbol: str) -> str:
    return symbol.replace(".", "").upper()


def login_or_raise():
    result = bs.login()
    if result.error_code != "0":
        raise RuntimeError(f"baostock login failed: {result.error_code} {result.error_msg}")


def query_trade_dates(start_date: str, end_date: str) -> List[str]:
    rs = bs.query_trade_dates(start_date=start_date, end_date=end_date)
    if rs.error_code != "0":
        raise RuntimeError(f"query_trade_dates failed: {rs.error_code} {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    df = pd.DataFrame(rows, columns=rs.fields)
    return df.loc[df["is_trading_day"] == "1", "calendar_date"].tolist()


def load_daily_cache(cache_path: Path) -> Dict[str, List[str]]:
    if not cache_path.exists():
        return {}
    with cache_path.open("rb") as fp:
        cache = pickle.load(fp)
    if not isinstance(cache, dict):
        raise ValueError(f"Invalid cache format: {cache_path}")
    return cache


def save_daily_cache(cache: Dict[str, List[str]], cache_path: Path):
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    with tmp_path.open("wb") as fp:
        pickle.dump(cache, fp)
    tmp_path.replace(cache_path)


def query_hs300_symbols(date: str, max_retry: int, retry_sleep: float) -> Set[str]:
    last_error = ""
    for _ in range(max_retry):
        rs = bs.query_hs300_stocks(date=date)
        if rs.error_code == "0":
            symbols = []
            while rs.next():
                symbols.append(normalize_symbol(rs.get_row_data()[1]))
            return set(symbols)
        last_error = f"{rs.error_code} {rs.error_msg}"
        if rs.error_code in {"10001001", "10002007"}:
            try:
                bs.logout()
            except Exception:
                pass
            time.sleep(retry_sleep)
            login_or_raise()
            continue
        time.sleep(retry_sleep)
    raise RuntimeError(f"query_hs300_stocks failed on {date}: {last_error}")


def build_membership_spans(
    trading_dates: List[str],
    max_retry: int,
    retry_sleep: float,
    request_sleep: float,
    daily_cache: Dict[str, List[str]],
    cache_path: Path,
    cache_flush_step: int,
) -> List[Tuple[str, str, str]]:
    active_starts: Dict[str, str] = {}
    prev_symbols: Set[str] | None = None
    prev_date: str | None = None
    spans: List[Tuple[str, str, str]] = []
    dirty_count = 0

    for trade_date in tqdm(trading_dates, desc="Download CSI300 history from baostock"):
        if trade_date in daily_cache:
            curr_symbols = set(daily_cache[trade_date])
        else:
            curr_symbols = query_hs300_symbols(trade_date, max_retry=max_retry, retry_sleep=retry_sleep)
            daily_cache[trade_date] = sorted(curr_symbols)
            dirty_count += 1
            if dirty_count >= cache_flush_step:
                save_daily_cache(daily_cache, cache_path)
                dirty_count = 0

        if prev_symbols is None:
            for symbol in curr_symbols:
                active_starts[symbol] = trade_date
        else:
            added = curr_symbols - prev_symbols
            removed = prev_symbols - curr_symbols

            for symbol in added:
                active_starts[symbol] = trade_date
            for symbol in removed:
                start_date = active_starts.pop(symbol, trade_date)
                spans.append((symbol, start_date, prev_date))

        prev_symbols = curr_symbols
        prev_date = trade_date

        if request_sleep > 0:
            time.sleep(request_sleep)

    if dirty_count > 0:
        save_daily_cache(daily_cache, cache_path)

    for symbol, start_date in active_starts.items():
        spans.append((symbol, start_date, DEFAULT_FAR_END_DATE))

    return spans


def main():
    parser = argparse.ArgumentParser(description="Download CSI300 historical constituents via baostock and save qlib instruments format.")
    parser.add_argument("--qlib_dir", required=True, help="Qlib data directory, e.g. ~/.qlib/qlib_data/cn_data")
    parser.add_argument("--start_date", default=DEFAULT_START_DATE, help=f"Start date, default: {DEFAULT_START_DATE}")
    parser.add_argument("--end_date", default=DEFAULT_END_DATE, help=f"End date, default: {DEFAULT_END_DATE}")
    parser.add_argument("--max_retry", type=int, default=5, help="Retry count for each date query")
    parser.add_argument("--retry_sleep", type=float, default=1.0, help="Sleep seconds between retries")
    parser.add_argument("--request_sleep", type=float, default=0.0, help="Sleep seconds between date requests")
    parser.add_argument(
        "--cache_path",
        default=None,
        help=f"Daily cache file path. Default: <qlib_dir>/{DEFAULT_CACHE_FILE_NAME}",
    )
    parser.add_argument("--cache_flush_step", type=int, default=20, help="Flush cache every N newly downloaded dates")
    args = parser.parse_args()

    qlib_dir = Path(args.qlib_dir).expanduser().resolve()
    instruments_dir = qlib_dir.joinpath("instruments")
    instruments_dir.mkdir(parents=True, exist_ok=True)
    output_path = instruments_dir.joinpath("csi300.txt")
    cache_path = (
        Path(args.cache_path).expanduser().resolve()
        if args.cache_path
        else qlib_dir.joinpath(DEFAULT_CACHE_FILE_NAME).resolve()
    )

    login_or_raise()

    try:
        daily_cache = load_daily_cache(cache_path)
        trading_dates = query_trade_dates(start_date=args.start_date, end_date=args.end_date)
        if not trading_dates:
            raise RuntimeError(f"No trading dates found in [{args.start_date}, {args.end_date}]")

        spans = build_membership_spans(
            trading_dates=trading_dates,
            max_retry=args.max_retry,
            retry_sleep=args.retry_sleep,
            request_sleep=args.request_sleep,
            daily_cache=daily_cache,
            cache_path=cache_path,
            cache_flush_step=max(1, args.cache_flush_step),
        )

        df = pd.DataFrame(spans, columns=["symbol", "start_date", "end_date"])
        df = df.sort_values(["symbol", "start_date", "end_date"])
        df.to_csv(output_path, sep="\t", index=False, header=False)
    finally:
        bs.logout()

    print(f"Saved {len(df)} membership spans to: {output_path}")
    print(f"Coverage trading dates: {trading_dates[0]} -> {trading_dates[-1]}")


if __name__ == "__main__":
    main()
