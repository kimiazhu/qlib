#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
将 Baostock 周频重建得到的 CSI300 区间文件，清洗并转换成 qlib instruments/csi300.txt 格式。

输入 CSV 格式:
instrument,in_date,last_seen_date

输出 qlib txt 格式（无表头，tab 分隔）:
SH600000    2006-01-06    2026-03-09

用法示例:
python convert_baostock_intervals_to_qlib.py \
  --input csi300_baostock_intervals.csv \
  --output csi300.txt \
  --clean-csv csi300_clean_intervals.csv \
  --summary-json csi300_clean_summary.json \
  --max-gap-days 21
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Tuple

import pandas as pd

REQUIRED_COLUMNS = ["instrument", "in_date", "last_seen_date"]


@dataclass
class Summary:
    input_rows: int
    rows_after_basic_clean: int
    duplicate_rows_removed: int
    invalid_rows_removed: int
    merged_segments_count: int
    output_rows: int
    instruments_count: int
    max_gap_days: int
    output_txt: str
    output_clean_csv: str


def normalize_instrument(code: str) -> str:
    if pd.isna(code):
        return ""

    s = str(code).strip().upper()
    if not s:
        return ""

    s = s.replace(" ", "")

    if (s.startswith("SH") or s.startswith("SZ")) and len(s) >= 8:
        return s

    if s.startswith("SH.") or s.startswith("SZ."):
        return s.replace(".", "")

    if s.endswith(".SH") and len(s) >= 9:
        return "SH" + s[:-3]

    if s.endswith(".SZ") and len(s) >= 9:
        return "SZ" + s[:-3]

    return s


def load_and_basic_clean(path: Path) -> Tuple[pd.DataFrame, dict]:
    df = pd.read_csv(path)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"输入文件缺少必要列: {missing}; 实际列: {list(df.columns)}")

    input_rows = len(df)

    df = df[REQUIRED_COLUMNS].copy()
    df["instrument"] = df["instrument"].map(normalize_instrument)
    df["in_date"] = pd.to_datetime(df["in_date"], errors="coerce")
    df["last_seen_date"] = pd.to_datetime(df["last_seen_date"], errors="coerce")

    valid_mask = (
        df["instrument"].ne("")
        & df["in_date"].notna()
        & df["last_seen_date"].notna()
        & (df["in_date"] <= df["last_seen_date"])
    )

    invalid_rows_removed = int((~valid_mask).sum())
    df = df.loc[valid_mask].copy()

    before_dedup = len(df)
    df = df.drop_duplicates()
    duplicate_rows_removed = before_dedup - len(df)

    df = df.sort_values(["instrument", "in_date", "last_seen_date"]).reset_index(drop=True)

    stats = {
        "input_rows": input_rows,
        "rows_after_basic_clean": len(df),
        "duplicate_rows_removed": duplicate_rows_removed,
        "invalid_rows_removed": invalid_rows_removed,
    }
    return df, stats


def merge_intervals(df: pd.DataFrame, max_gap_days: int) -> Tuple[pd.DataFrame, int]:
    merged_rows: List[Tuple[str, pd.Timestamp, pd.Timestamp]] = []
    merged_segments_count = 0

    for instrument, g in df.groupby("instrument", sort=True):
        rows = g.sort_values(["in_date", "last_seen_date"]).reset_index(drop=True)

        cur_start = rows.loc[0, "in_date"]
        cur_end = rows.loc[0, "last_seen_date"]

        for i in range(1, len(rows)):
            next_start = rows.loc[i, "in_date"]
            next_end = rows.loc[i, "last_seen_date"]

            gap = (next_start - cur_end).days

            if gap <= max_gap_days:
                old_end = cur_end
                cur_end = max(cur_end, next_end)
                if next_start > old_end or next_end > old_end:
                    merged_segments_count += 1
            else:
                merged_rows.append((instrument, cur_start, cur_end))
                cur_start = next_start
                cur_end = next_end

        merged_rows.append((instrument, cur_start, cur_end))

    out = pd.DataFrame(merged_rows, columns=["instrument", "in_date", "last_seen_date"])
    out = out.sort_values(["instrument", "in_date", "last_seen_date"]).reset_index(drop=True)
    return out, merged_segments_count


def write_outputs(
    df: pd.DataFrame,
    output_txt: Path,
    clean_csv: Path,
    summary_json: Path,
    summary: Summary,
) -> None:
    clean_df = df.copy()
    clean_df["in_date"] = clean_df["in_date"].dt.strftime("%Y-%m-%d")
    clean_df["last_seen_date"] = clean_df["last_seen_date"].dt.strftime("%Y-%m-%d")

    clean_df.to_csv(clean_csv, index=False)

    with output_txt.open("w", encoding="utf-8") as f:
        for row in clean_df.itertuples(index=False):
            f.write(f"{row.instrument}\t{row.in_date}\t{row.last_seen_date}\n")

    with summary_json.open("w", encoding="utf-8") as f:
        json.dump(asdict(summary), f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="清洗 Baostock CSI300 区间文件并转换为 qlib csi300.txt 格式"
    )
    parser.add_argument("--input", required=True, help="输入区间 CSV，如 csi300_baostock_intervals.csv")
    parser.add_argument("--output", required=True, help="输出 qlib txt，如 csi300.txt")
    parser.add_argument("--clean-csv", default="csi300_clean_intervals.csv", help="输出清洗后的 CSV")
    parser.add_argument("--summary-json", default="csi300_clean_summary.json", help="输出统计摘要 JSON")
    parser.add_argument(
        "--max-gap-days",
        type=int,
        default=21,
        help="合并相邻区间的最大允许间隔天数，默认 21（适合周频采样）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    input_path = Path(args.input)
    output_txt = Path(args.output)
    clean_csv = Path(args.clean_csv)
    summary_json = Path(args.summary_json)

    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    df, stats = load_and_basic_clean(input_path)
    merged_df, merged_segments_count = merge_intervals(df, max_gap_days=args.max_gap_days)

    summary = Summary(
        input_rows=stats["input_rows"],
        rows_after_basic_clean=stats["rows_after_basic_clean"],
        duplicate_rows_removed=stats["duplicate_rows_removed"],
        invalid_rows_removed=stats["invalid_rows_removed"],
        merged_segments_count=merged_segments_count,
        output_rows=len(merged_df),
        instruments_count=merged_df["instrument"].nunique(),
        max_gap_days=args.max_gap_days,
        output_txt=str(output_txt),
        output_clean_csv=str(clean_csv),
    )

    write_outputs(
        df=merged_df,
        output_txt=output_txt,
        clean_csv=clean_csv,
        summary_json=summary_json,
        summary=summary,
    )

    print("转换完成")
    print(f"输入文件: {input_path}")
    print(f"输出 qlib txt: {output_txt}")
    print(f"输出清洗 CSV: {clean_csv}")
    print(f"输出摘要 JSON: {summary_json}")
    print(f"原始行数: {summary.input_rows}")
    print(f"基础清洗后行数: {summary.rows_after_basic_clean}")
    print(f"无效行删除: {summary.invalid_rows_removed}")
    print(f"重复行删除: {summary.duplicate_rows_removed}")
    print(f"合并段次数: {summary.merged_segments_count}")
    print(f"最终区间行数: {summary.output_rows}")
    print(f"证券数量: {summary.instruments_count}")
    print(f"max_gap_days: {summary.max_gap_days}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
