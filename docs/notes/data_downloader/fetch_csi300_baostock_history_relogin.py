#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

CSI300_LAUNCH_DATE = "2005-04-08"
# 实测与公开示例都表明，baostock 这条接口的可用历史通常从 2006 年开始稳定返回
BAOSTOCK_EARLIEST_WORKING_DATE = "2006-01-01"
AUTH_ERRORS = {"10001001"}  # 未登录
RETRYABLE_ERRORS = {"10002007"}  # 网络接收错误 / 会话超时?


def _ensure_baostock():
    try:
        import baostock as bs  # type: ignore
    except Exception as e:
        raise RuntimeError("未安装 baostock，请先执行: pip install baostock") from e
    return bs


class BaoClient:
    def __init__(self):
        self.bs = _ensure_baostock()
        self.logged_in = False
        self.login_count = 0
        self.relogin_count = 0

    def login(self):
        if self.logged_in:
            return
        lg = self.bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login failed: {lg.error_code} {lg.error_msg}")
        self.logged_in = True
        self.login_count += 1

    def logout(self):
        if self.logged_in:
            try:
                self.bs.logout()
            finally:
                self.logged_in = False

    def relogin(self):
        self.logout()
        time.sleep(0.3)
        self.login()
        self.relogin_count += 1

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.logout()


def iter_trade_days(client: BaoClient, start: str, end: str) -> List[str]:
    client.login()
    rs = client.bs.query_trade_dates(start_date=start, end_date=end)
    if rs.error_code != "0":
        raise RuntimeError(f"query_trade_dates failed: {rs.error_code} {rs.error_msg}")
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    df = pd.DataFrame(rows, columns=rs.fields)
    if df.empty:
        raise RuntimeError("query_trade_dates 返回为空")
    return df.loc[df["is_trading_day"] == "1", "calendar_date"].tolist()


def sample_dates(trade_days: List[str], freq: str) -> List[str]:
    s = pd.Series(pd.to_datetime(trade_days))
    if freq == "daily":
        out = s
    elif freq == "weekly":
        out = s.groupby(s.dt.to_period("W-FRI")).max()
    elif freq == "monthly":
        out = s.groupby(s.dt.to_period("M")).max()
    else:
        raise ValueError(f"unsupported freq: {freq}")
    return out.dt.strftime("%Y-%m-%d").tolist()


def _read_resultset_to_df(rs) -> pd.DataFrame:
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=rs.fields)


def fetch_snapshot(client: BaoClient, date: str, max_retry: int = 2) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    """返回 (df或None, 备注)；None+备注='empty' 表示空快照。"""
    last_err = None
    for attempt in range(max_retry + 1):
        client.login()
        rs = client.bs.query_hs300_stocks(date=date)

        if rs.error_code == "0":
            df = _read_resultset_to_df(rs)
            if df.empty:
                return None, "empty"
            if "code" not in df.columns:
                raise RuntimeError(f"{date} 返回字段异常: {df.columns.tolist()}")
            df = df.copy()
            df["query_date"] = date
            return df, None

        # 认证/网络错误，尝试重登后重试
        if rs.error_code in AUTH_ERRORS | RETRYABLE_ERRORS and attempt < max_retry:
            last_err = f"{rs.error_code} {rs.error_msg}"
            client.relogin()
            time.sleep(0.2 * (attempt + 1))
            continue

        last_err = f"{rs.error_code} {rs.error_msg}"
        break

    raise RuntimeError(f"query_hs300_stocks({date}) failed after retry: {last_err}")


def normalize_code(bs_code: str) -> str:
    market, code = bs_code.split(".")
    return market.upper() + code


def compress_to_intervals(snapshot_df: pd.DataFrame, freq: str) -> pd.DataFrame:
    gap_threshold = {"daily": 10, "weekly": 14, "monthly": 45}[freq]

    df = snapshot_df.copy()
    df["instrument"] = df["code"].map(normalize_code)
    df["query_date"] = pd.to_datetime(df["query_date"])
    df = df.sort_values(["instrument", "query_date"]).reset_index(drop=True)

    out = []
    for code, g in df.groupby("instrument", sort=True):
        dates = list(g["query_date"])
        start = dates[0]
        prev = dates[0]
        for d in dates[1:]:
            if (d - prev).days > gap_threshold:
                out.append([code, start, prev])
                start = d
            prev = d
        out.append([code, start, prev])

    result = pd.DataFrame(out, columns=["instrument", "in_date", "last_seen_date"])
    result["in_date"] = pd.to_datetime(result["in_date"]).dt.strftime("%Y-%m-%d")
    result["last_seen_date"] = pd.to_datetime(result["last_seen_date"]).dt.strftime("%Y-%m-%d")
    return result.sort_values(["instrument", "in_date"]).reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="从 baostock 获取 CSI300 历史成分快照并压缩为区间（自动重登录版）")
    parser.add_argument("--start", default="2005-01-01")
    parser.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    parser.add_argument("--freq", choices=["daily", "weekly", "monthly"], default="weekly")
    parser.add_argument("--snapshot-output", default="csi300_baostock_snapshots.csv")
    parser.add_argument("--interval-output", default="csi300_baostock_intervals.csv")
    parser.add_argument("--meta-output", default="csi300_baostock_meta.json")
    args = parser.parse_args()

    req_start = pd.Timestamp(args.start)
    req_end = pd.Timestamp(args.end)
    launch = pd.Timestamp(CSI300_LAUNCH_DATE)
    earliest = pd.Timestamp(BAOSTOCK_EARLIEST_WORKING_DATE)
    actual_start = max(req_start, launch, earliest)
    if actual_start > req_end:
        raise RuntimeError(
            f"开始日期 {args.start} 晚于可用区间；当前脚本将从 {actual_start.strftime('%Y-%m-%d')} 开始抓取"
        )

    with BaoClient() as client:
        trade_days = iter_trade_days(client, actual_start.strftime("%Y-%m-%d"), args.end)
        dates = sample_dates(trade_days, args.freq)

        frames = []
        empty_dates = []
        failures = []
        for i, d in enumerate(dates, 1):
            try:
                snap, note = fetch_snapshot(client, d)
                if snap is None:
                    empty_dates.append(d)
                    print(f"[{i}/{len(dates)}] empty {d}: 快照为空，已跳过")
                    continue
                frames.append(snap)
                print(f"[{i}/{len(dates)}] ok {d}: {len(snap)} rows")
            except Exception as e:
                failures.append({"date": d, "error": str(e)})
                print(f"[{i}/{len(dates)}] fail {d}: {e}")
                # 失败后主动重登一次，防止连续传播
                try:
                    client.relogin()
                except Exception:
                    pass

        if not frames:
            raise RuntimeError("没有成功抓取到任何快照")

        snapshots = pd.concat(frames, ignore_index=True)
        snapshots.to_csv(args.snapshot_output, index=False, encoding="utf-8-sig")

        intervals = compress_to_intervals(snapshots, args.freq)
        intervals.to_csv(args.interval_output, index=False, encoding="utf-8-sig")

        meta = {
            "source": "baostock.query_hs300_stocks(date=...)",
            "requested_start": args.start,
            "actual_start": actual_start.strftime("%Y-%m-%d"),
            "end": args.end,
            "freq": args.freq,
            "sampled_dates": len(dates),
            "success_dates": len(frames),
            "empty_dates": len(empty_dates),
            "failed_dates": len(failures),
            "login_count": client.login_count,
            "relogin_count": client.relogin_count,
            "empty_date_examples": empty_dates[:20],
            "failure_examples": failures[:20],
            "note": (
                f"沪深300于 {CSI300_LAUNCH_DATE} 正式发布；公开示例显示 baostock 这条接口的历史结果通常从 2006 年开始可稳定获取，"
                f"因此脚本默认会从 {BAOSTOCK_EARLIEST_WORKING_DATE} 起抓取。"
                " 这是按采样日重建的历史区间，不等同于中证官网公告口径下的严格生效日。"
            ),
        }
        Path(args.meta_output).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved snapshots -> {args.snapshot_output}")
        print(f"saved intervals -> {args.interval_output}")
        print(f"saved meta -> {args.meta_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
