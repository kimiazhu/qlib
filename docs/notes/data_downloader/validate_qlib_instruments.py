#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
校验 qlib instruments/csi300.txt 是否存在以下问题：
1. 重叠区间
2. 反向区间（start > end）
3. 重复记录
4. 字段数不正确
5. 日期格式非法
6. 同一股票区间未排序（会自动按日期检查，不要求原文件预排序）

输入文件格式（无表头，Tab 分隔）:
SH600000    2006-01-06    2026-03-09

输出：
- 终端摘要
- JSON 报告
- 可选 CSV 明细（重复、反向、重叠、格式错误）

用法示例：
python validate_qlib_instruments.py \
  --input csi300.txt \
  --report-json csi300_validation_report.json \
  --detail-dir validation_details
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd


@dataclass
class Summary:
    input_file: str
    total_lines: int
    valid_rows: int
    unique_instruments: int
    format_error_count: int
    invalid_date_count: int
    reversed_interval_count: int
    duplicate_record_count: int
    overlap_pair_count: int
    has_issues: bool
    detail_dir: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验 qlib instruments txt 文件")
    parser.add_argument("--input", required=True, help="输入 qlib txt，例如 csi300.txt")
    parser.add_argument(
        "--report-json",
        default="qlib_instruments_validation_report.json",
        help="输出 JSON 报告路径",
    )
    parser.add_argument(
        "--detail-dir",
        default="validation_details",
        help="输出问题明细目录",
    )
    return parser.parse_args()


def write_csv(path: Path, rows: List[dict], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    report_json = Path(args.report_json)
    detail_dir = Path(args.detail_dir)

    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    total_lines = 0
    format_errors: List[dict] = []
    invalid_dates: List[dict] = []
    reversed_intervals: List[dict] = []
    valid_rows: List[dict] = []

    with input_path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.rstrip("\n\r")
            if not line.strip():
                continue

            total_lines += 1
            parts = line.split("\t")

            if len(parts) != 3:
                format_errors.append(
                    {
                        "line_no": lineno,
                        "raw_line": line,
                        "field_count": len(parts),
                        "reason": "字段数不是 3，需为 instrument<TAB>start_date<TAB>end_date",
                    }
                )
                continue

            instrument, start_s, end_s = [p.strip() for p in parts]

            start_dt = pd.to_datetime(start_s, errors="coerce")
            end_dt = pd.to_datetime(end_s, errors="coerce")

            if not instrument:
                format_errors.append(
                    {
                        "line_no": lineno,
                        "raw_line": line,
                        "field_count": 3,
                        "reason": "instrument 为空",
                    }
                )
                continue

            if pd.isna(start_dt) or pd.isna(end_dt):
                invalid_dates.append(
                    {
                        "line_no": lineno,
                        "instrument": instrument,
                        "start_date": start_s,
                        "end_date": end_s,
                        "reason": "日期无法解析",
                    }
                )
                continue

            if start_dt > end_dt:
                reversed_intervals.append(
                    {
                        "line_no": lineno,
                        "instrument": instrument,
                        "start_date": start_s,
                        "end_date": end_s,
                        "reason": "start_date > end_date",
                    }
                )
                continue

            valid_rows.append(
                {
                    "line_no": lineno,
                    "instrument": instrument,
                    "start_date": start_dt,
                    "end_date": end_dt,
                    "start_date_str": start_dt.strftime("%Y-%m-%d"),
                    "end_date_str": end_dt.strftime("%Y-%m-%d"),
                }
            )

    df = pd.DataFrame(valid_rows)

    duplicate_records: List[dict] = []
    overlap_pairs: List[dict] = []

    if not df.empty:
        dup_mask = df.duplicated(subset=["instrument", "start_date", "end_date"], keep=False)
        dup_df = df.loc[dup_mask].copy().sort_values(["instrument", "start_date", "end_date", "line_no"])
        for _, row in dup_df.iterrows():
            duplicate_records.append(
                {
                    "line_no": int(row["line_no"]),
                    "instrument": row["instrument"],
                    "start_date": row["start_date_str"],
                    "end_date": row["end_date_str"],
                }
            )

        for instrument, g in df.groupby("instrument", sort=True):
            g = g.sort_values(["start_date", "end_date", "line_no"]).reset_index(drop=True)
            prev = None
            for i in range(len(g)):
                cur = g.loc[i]
                if prev is not None:
                    # 若当前 start <= 前一段 end，则存在重叠
                    if cur["start_date"] <= prev["end_date"]:
                        overlap_pairs.append(
                            {
                                "instrument": instrument,
                                "prev_line_no": int(prev["line_no"]),
                                "prev_start_date": prev["start_date_str"],
                                "prev_end_date": prev["end_date_str"],
                                "curr_line_no": int(cur["line_no"]),
                                "curr_start_date": cur["start_date_str"],
                                "curr_end_date": cur["end_date_str"],
                            }
                        )
                        # 保留 end 更晚的区间作为后续比较对象
                        if cur["end_date"] > prev["end_date"]:
                            prev = cur
                    else:
                        prev = cur
                else:
                    prev = cur

    detail_dir.mkdir(parents=True, exist_ok=True)

    write_csv(
        detail_dir / "format_errors.csv",
        format_errors,
        ["line_no", "raw_line", "field_count", "reason"],
    )
    write_csv(
        detail_dir / "invalid_dates.csv",
        invalid_dates,
        ["line_no", "instrument", "start_date", "end_date", "reason"],
    )
    write_csv(
        detail_dir / "reversed_intervals.csv",
        reversed_intervals,
        ["line_no", "instrument", "start_date", "end_date", "reason"],
    )
    write_csv(
        detail_dir / "duplicate_records.csv",
        duplicate_records,
        ["line_no", "instrument", "start_date", "end_date"],
    )
    write_csv(
        detail_dir / "overlap_pairs.csv",
        overlap_pairs,
        [
            "instrument",
            "prev_line_no",
            "prev_start_date",
            "prev_end_date",
            "curr_line_no",
            "curr_start_date",
            "curr_end_date",
        ],
    )

    summary = Summary(
        input_file=str(input_path),
        total_lines=total_lines,
        valid_rows=int(len(df)),
        unique_instruments=int(df["instrument"].nunique()) if not df.empty else 0,
        format_error_count=len(format_errors),
        invalid_date_count=len(invalid_dates),
        reversed_interval_count=len(reversed_intervals),
        duplicate_record_count=len(duplicate_records),
        overlap_pair_count=len(overlap_pairs),
        has_issues=any(
            [
                len(format_errors),
                len(invalid_dates),
                len(reversed_intervals),
                len(duplicate_records),
                len(overlap_pairs),
            ]
        ),
        detail_dir=str(detail_dir),
    )

    report = asdict(summary)
    report["detail_files"] = {
        "format_errors": str(detail_dir / "format_errors.csv"),
        "invalid_dates": str(detail_dir / "invalid_dates.csv"),
        "reversed_intervals": str(detail_dir / "reversed_intervals.csv"),
        "duplicate_records": str(detail_dir / "duplicate_records.csv"),
        "overlap_pairs": str(detail_dir / "overlap_pairs.csv"),
    }

    with report_json.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("校验完成")
    print(f"输入文件: {input_path}")
    print(f"总行数: {summary.total_lines}")
    print(f"有效记录数: {summary.valid_rows}")
    print(f"股票数量: {summary.unique_instruments}")
    print(f"字段格式错误: {summary.format_error_count}")
    print(f"非法日期: {summary.invalid_date_count}")
    print(f"反向区间: {summary.reversed_interval_count}")
    print(f"重复记录: {summary.duplicate_record_count}")
    print(f"重叠区间对: {summary.overlap_pair_count}")
    print(f"JSON 报告: {report_json}")
    print(f"明细目录: {detail_dir}")
    print(f"是否存在问题: {'是' if summary.has_issues else '否'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
