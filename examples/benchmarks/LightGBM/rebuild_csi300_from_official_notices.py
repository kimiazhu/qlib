#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从中证指数官网公告“全量回放”重建 CSI 300（沪深300）历史成分。

设计目标
========
这不是“修补坏文件”的脚本，而是一个 **严格回放器**：
1. 以 **官方初始样本** 为起点（2005-04-08 发布日样本，需用户提供官方来源文件）；
2. 逐个回放 **官网公告 / PDF 附件**；
3. 输出符合 Qlib instruments 规范的历史区间文件：
   <TICKER>\t<START_DATE>\t<END_DATE>

为什么要这样设计
================
仅靠一份“当前成分股”或一份已经脏掉的历史文件，无法严格还原全历史；
真正可审计的做法，必须是：
    初始官方样本 + 后续每次官方调整公告 + 按生效日逐次回放。

你需要准备的官方材料
====================
A. 初始样本文件（必须）
   - 2005-04-08 沪深300正式发布时的官方样本名单
   - 可为 txt/csv/xlsx，文件里至少能识别出 6 位证券代码

B. 官方调整公告（必须）
   推荐准备一个 manifest.csv，逐条列出官方公告：

   effective_date,pdf_url,kind,title
   2005-06-13,https://...pdf,periodic,2005年6月定期调整
   2005-12-12,https://...pdf,periodic,2005年12月定期调整
   2006-03-01,https://...pdf,temporary,某次临时调整

   其中：
   - effective_date: 生效日（建议你从公告正文核实后填入；periodic 若不填，脚本可按规则推断）
   - pdf_url: 官网公告附件 PDF 的官方 URL（推荐 oss-ch.csindex.com.cn / csindex.com.cn）
   - kind: periodic / temporary
   - title: 可选，便于审计

说明：
- 对“定期调整”，脚本可以根据中证规则自动推断为每年 6 月 / 12 月第二个星期五的下一交易日。
- 对“临时调整”，强烈建议在 manifest 里显式给 effective_date；否则很难保证 100% 严格。

PDF 解析假设
============
脚本优先解析包含“沪深300指数样本调整名单”之类表格附件的 PDF。
常见表格结构：
    调出名单 | 调入名单
    证券代码 | 证券名称 | 证券代码 | 证券名称

注意：
- 不同年份 PDF 版式可能不同；因此脚本实现了多套回退解析逻辑。
- 你应在首次跑完整历史后执行 --audit，检查每个事件的解析结果。

依赖
====
建议安装：
    pip install requests pandas openpyxl pymupdf pdfplumber

其中：
- PyMuPDF (fitz) 是首选 PDF 提取器；
- pdfplumber 作为备选；
- openpyxl 用于读 xlsx 初始名单。

示例
====
1) 严格重建：
python rebuild_csi300_from_official_notices.py \
  --seed launch_20050408.xlsx \
  --manifest official_manifest.csv \
  --out csi300.strict.txt \
  --audit-dir audit_out

2) 如果 manifest 中 periodic 事件没填 effective_date，可让脚本自动推断：
python rebuild_csi300_from_official_notices.py \
  --seed launch_20050408.txt \
  --manifest official_manifest.csv \
  --infer-periodic-effective-date \
  --out csi300.strict.txt
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Optional deps
try:
    import requests  # type: ignore
except Exception:
    requests = None

try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None

try:
    import fitz  # PyMuPDF  # type: ignore
except Exception:
    fitz = None

try:
    import pdfplumber  # type: ignore
except Exception:
    pdfplumber = None

CODE_RE = re.compile(r"(?<!\d)(?:SH|SZ)?(\d{6})(?!\d)")
DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
TIMESTAMP_IN_URL_RE = re.compile(r"/(20\d{12,14})-")

START_DATE = dt.date(2005, 4, 8)  # 沪深300正式发布日
DEFAULT_END_DATE = dt.date(2099, 12, 31)


@dataclass
class Event:
    effective_date: dt.date
    pdf_url: str
    kind: str = "periodic"  # periodic / temporary
    title: str = ""


@dataclass
class ParsedEvent:
    event: Event
    added: List[str]
    removed: List[str]
    parser_used: str
    raw_text_preview: str


class RebuildError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def normalize_code(code: str) -> str:
    code = code.strip().upper()
    m = re.fullmatch(r"(?:SH|SZ)?(\d{6})", code)
    if not m:
        raise ValueError(f"非法证券代码: {code}")
    raw = m.group(1)
    if raw.startswith(("5", "6", "9")):
        return f"SH{raw}"
    return f"SZ{raw}"


def parse_date(s: str) -> dt.date:
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    m = DATE_RE.search(s)
    if m:
        y, mo, d = map(int, m.groups())
        return dt.date(y, mo, d)
    raise ValueError(f"无法解析日期: {s}")


def second_friday(year: int, month: int) -> dt.date:
    first = dt.date(year, month, 1)
    offset = (4 - first.weekday()) % 7  # Friday=4
    first_friday = first + dt.timedelta(days=offset)
    return first_friday + dt.timedelta(days=7)


def next_weekday(d: dt.date) -> dt.date:
    x = d + dt.timedelta(days=1)
    while x.weekday() >= 5:
        x += dt.timedelta(days=1)
    return x


def infer_periodic_effective_date_from_url_or_title(pdf_url: str, title: str = "") -> dt.date:
    """
    对定期调整：公告通常在 5/11 月底发布，真正生效日在随后的 6/12 月第二个星期五后的下一交易日。
    这里根据 URL 时间戳或标题中的日期，推断所属半年度并给出生效日。
    """
    hint_date: Optional[dt.date] = None

    m = TIMESTAMP_IN_URL_RE.search(pdf_url)
    if m:
        ts = m.group(1)
        # 20251128165753 / 202511281657
        year = int(ts[:4])
        month = int(ts[4:6])
        day = int(ts[6:8])
        hint_date = dt.date(year, month, day)
    else:
        m2 = DATE_RE.search(title)
        if m2:
            y, mo, d = map(int, m2.groups())
            hint_date = dt.date(y, mo, d)

    if hint_date is None:
        raise RebuildError(
            f"无法从 URL / 标题推断定期调整所属半年度，请在 manifest 里显式填写 effective_date: {pdf_url}"
        )

    if hint_date.month in (4, 5, 6):
        rebalance_month = 6
    elif hint_date.month in (10, 11, 12):
        rebalance_month = 12
    else:
        raise RebuildError(
            f"公告日期 {hint_date} 不像沪深300半年定调窗口，请手工指定 effective_date: {pdf_url}"
        )

    sf = second_friday(hint_date.year, rebalance_month)
    return next_weekday(sf)


def read_seed_codes(path: Path) -> List[str]:
    suffix = path.suffix.lower()
    text = ""

    if suffix in {".txt", ".csv"}:
        text = path.read_text(encoding="utf-8", errors="ignore")
        codes = [normalize_code(x) for x in CODE_RE.findall(text)]
        codes = sorted(dict.fromkeys(codes))
        if not codes:
            raise RebuildError(f"初始样本文件未识别到任何 6 位证券代码: {path}")
        return codes

    if suffix in {".xlsx", ".xls"}:
        if pd is None:
            raise RebuildError("读取 xlsx/xls 需要 pandas/openpyxl，请先安装。")
        xl = pd.ExcelFile(path)
        found: List[str] = []
        for sheet in xl.sheet_names:
            df = xl.parse(sheet)
            for col in df.columns:
                series = df[col].astype(str)
                for item in series:
                    for raw in CODE_RE.findall(item):
                        found.append(normalize_code(raw))
        found = sorted(dict.fromkeys(found))
        if not found:
            raise RebuildError(f"初始样本 Excel 未识别到任何 6 位证券代码: {path}")
        return found

    raise RebuildError(f"不支持的初始样本文件格式: {path}")


def load_manifest(path: Path, infer_periodic_effective_date: bool) -> List[Event]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    events: List[Event] = []
    for i, row in enumerate(rows, start=2):
        pdf_url = (row.get("pdf_url") or "").strip()
        if not pdf_url:
            raise RebuildError(f"manifest 第 {i} 行缺少 pdf_url")
        kind = (row.get("kind") or "periodic").strip().lower()
        title = (row.get("title") or "").strip()
        eff = (row.get("effective_date") or "").strip()

        if eff:
            effective_date = parse_date(eff)
        else:
            if kind == "periodic" and infer_periodic_effective_date:
                effective_date = infer_periodic_effective_date_from_url_or_title(pdf_url, title)
            else:
                raise RebuildError(
                    f"manifest 第 {i} 行未提供 effective_date，且当前无法自动推断: {pdf_url}"
                )

        events.append(Event(effective_date=effective_date, pdf_url=pdf_url, kind=kind, title=title))

    events.sort(key=lambda x: (x.effective_date, x.pdf_url))
    return events


def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    if requests is None:
        raise RebuildError("下载 PDF 需要 requests，请先安装。")
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CSI300-history-rebuilder/1.0)",
        "Accept": "application/pdf,application/octet-stream,*/*",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def extract_text_pymupdf(pdf_bytes: bytes) -> str:
    if fitz is None:
        raise RebuildError("PyMuPDF 不可用")
    out: List[str] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            out.append(page.get_text("text"))
    return "\n".join(out)


def extract_text_pdfplumber(pdf_bytes: bytes) -> str:
    if pdfplumber is None:
        raise RebuildError("pdfplumber 不可用")
    out: List[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            out.append(txt)
    return "\n".join(out)


def extract_pdf_text(pdf_bytes: bytes) -> Tuple[str, str]:
    errs: List[str] = []
    if fitz is not None:
        try:
            return extract_text_pymupdf(pdf_bytes), "pymupdf"
        except Exception as e:
            errs.append(f"pymupdf: {e}")
    if pdfplumber is not None:
        try:
            return extract_text_pdfplumber(pdf_bytes), "pdfplumber"
        except Exception as e:
            errs.append(f"pdfplumber: {e}")
    raise RebuildError("PDF 文本提取失败；请安装 pymupdf 或 pdfplumber。错误: " + " | ".join(errs))


SECTION_START_PATTERNS = [
    re.compile(r"沪深\s*300\s*指数样本调整名单"),
    re.compile(r"沪深300指数样本调整名单"),
    re.compile(r"沪深\s*300\s*样本调整名单"),
]
SECTION_END_PATTERNS = [
    re.compile(r"中证\s*500\s*指数样本调整名单"),
    re.compile(r"中证500指数样本调整名单"),
    re.compile(r"沪深\s*300\s*指数备选名单"),
]


def find_relevant_section(text: str) -> str:
    start = -1
    for p in SECTION_START_PATTERNS:
        m = p.search(text)
        if m:
            start = m.start()
            break
    if start == -1:
        # 如果找不到明确 section，就退化为全量文本
        return text

    sub = text[start:]
    ends = [m.start() for p in SECTION_END_PATTERNS for m in [p.search(sub)] if m]
    if ends:
        sub = sub[: min(ends)]
    return sub


REMOVE_MARKERS = [
    re.compile(r"调出名单"),
    re.compile(r"调出"),
    re.compile(r"剔除名单"),
]
ADD_MARKERS = [
    re.compile(r"调入名单"),
    re.compile(r"调入"),
    re.compile(r"纳入名单"),
    re.compile(r"替换进入"),
]


def _split_by_markers(section: str) -> Optional[Tuple[str, str]]:
    remove_pos = None
    add_pos = None
    for p in REMOVE_MARKERS:
        m = p.search(section)
        if m:
            remove_pos = m.start()
            break
    for p in ADD_MARKERS:
        m = p.search(section)
        if m:
            add_pos = m.start()
            break
    if remove_pos is None or add_pos is None:
        return None

    if remove_pos < add_pos:
        return section[remove_pos:add_pos], section[add_pos:]
    return section[remove_pos:], section[add_pos:remove_pos]


def parse_codes_from_block(block: str) -> List[str]:
    codes = [normalize_code(x) for x in CODE_RE.findall(block)]
    # 保序去重
    return list(dict.fromkeys(codes))


def parse_periodic_alternating(section: str) -> Optional[Tuple[List[str], List[str], str]]:
    """
    适合常见四列表格被线性抽取后的场景：
    out1 in1 out2 in2 ...
    """
    codes = [normalize_code(x) for x in CODE_RE.findall(section)]
    if len(codes) < 2:
        return None
    removed = list(dict.fromkeys(codes[0::2]))
    added = list(dict.fromkeys(codes[1::2]))
    if not removed or not added:
        return None
    return added, removed, "alternating"


def parse_periodic_by_markers(section: str) -> Optional[Tuple[List[str], List[str], str]]:
    res = _split_by_markers(section)
    if not res:
        return None
    remove_block, add_block = res
    removed = parse_codes_from_block(remove_block)
    added = parse_codes_from_block(add_block)
    if not removed or not added:
        return None
    return added, removed, "marker_split"


TEMP_EFFECTIVE_RE = re.compile(
    r"(?:于|自)?\s*(20\d{2}[年\-/.]\d{1,2}[月\-/.]\d{1,2}日?)\s*(?:起|实施|生效|调整|收市后)")


def infer_temporary_effective_date_from_text(text: str) -> Optional[dt.date]:
    m = TEMP_EFFECTIVE_RE.search(text)
    if not m:
        return None
    try:
        return parse_date(m.group(1))
    except Exception:
        return None


def parse_temporary(section: str) -> Optional[Tuple[List[str], List[str], str]]:
    """
    临时调整常见表达：
    - 将 XXX 调出沪深300指数样本，YYY 调入
    - 因 ...，剔除 XXX，由 YYY 替代
    这里只做代码级别解析，具体生效日仍建议用 manifest 显式给出。
    """
    # 优先按 marker split
    out = parse_periodic_by_markers(section)
    if out:
        added, removed, _ = out
        if len(added) <= 5 and len(removed) <= 5:
            return added, removed, "temporary_marker_split"

    codes = [normalize_code(x) for x in CODE_RE.findall(section)]
    if len(codes) == 2:
        # 常见 1 出 1 进
        return [codes[1]], [codes[0]], "temporary_two_code"
    if len(codes) == 4:
        return list(dict.fromkeys(codes[1::2])), list(dict.fromkeys(codes[0::2])), "temporary_four_code"
    return None


def parse_event_from_pdf(event: Event, pdf_bytes: bytes) -> ParsedEvent:
    text, extractor = extract_pdf_text(pdf_bytes)
    section = find_relevant_section(text)
    compact = re.sub(r"[ \t\u3000]+", " ", section)

    parser_result: Optional[Tuple[List[str], List[str], str]] = None

    if event.kind == "periodic":
        # 先试 marker split，再试 alternating
        parser_result = parse_periodic_by_markers(compact)
        if parser_result is None:
            parser_result = parse_periodic_alternating(compact)
    else:
        parser_result = parse_temporary(compact)
        if parser_result is None:
            # 临时调整如果版式也恰好是普通名单，也退回公共解析
            parser_result = parse_periodic_by_markers(compact)
        if parser_result is None:
            parser_result = parse_periodic_alternating(compact)

    if parser_result is None:
        preview = compact[:1000]
        raise RebuildError(
            f"无法解析公告 PDF：{event.pdf_url}\n"
            f"建议：检查 PDF 文字提取是否正常，或在 manifest 中人工拆分该事件。\n"
            f"文本预览：\n{preview}"
        )

    added, removed, parser_used = parser_result
    return ParsedEvent(
        event=event,
        added=added,
        removed=removed,
        parser_used=f"{extractor}+{parser_used}",
        raw_text_preview=compact[:1500],
    )


def replay_history(seed_codes: Sequence[str], parsed_events: Sequence[ParsedEvent], end_date: dt.date) -> List[Tuple[str, dt.date, dt.date]]:
    active: Set[str] = set(seed_codes)
    intervals: Dict[str, List[List[dt.date]]] = {code: [[START_DATE, end_date]] for code in active}

    def ensure_interval(code: str) -> None:
        intervals.setdefault(code, [])

    for pe in sorted(parsed_events, key=lambda x: x.event.effective_date):
        eff = pe.event.effective_date
        prev_day = eff - dt.timedelta(days=1)

        # 先调出
        for code in pe.removed:
            ensure_interval(code)
            if code not in active:
                raise RebuildError(
                    f"回放失败：{eff} 事件试图调出未在指数内的股票 {code}。\n"
                    f"对应公告：{pe.event.pdf_url}"
                )
            if not intervals[code]:
                raise RebuildError(f"内部状态错误：{code} 没有开放区间却处于 active")
            last = intervals[code][-1]
            if last[1] != end_date:
                raise RebuildError(f"内部状态错误：{code} 最后区间不是开放态")
            if prev_day < last[0]:
                raise RebuildError(
                    f"回放失败：{code} 的区间起点 {last[0]} 晚于调出前一日 {prev_day}。\n"
                    f"对应公告：{pe.event.pdf_url}"
                )
            last[1] = prev_day
            active.remove(code)

        # 再调入
        for code in pe.added:
            ensure_interval(code)
            if code in active:
                raise RebuildError(
                    f"回放失败：{eff} 事件试图调入已在指数内的股票 {code}。\n"
                    f"对应公告：{pe.event.pdf_url}"
                )
            intervals[code].append([eff, end_date])
            active.add(code)

        if len(active) != 300:
            raise RebuildError(
                f"回放失败：事件 {eff} 之后当前成分数变为 {len(active)}，不等于 300。\n"
                f"对应公告：{pe.event.pdf_url}"
            )

    rows: List[Tuple[str, dt.date, dt.date]] = []
    for code, spans in intervals.items():
        for start, stop in spans:
            rows.append((code, start, stop))
    rows.sort(key=lambda x: (x[0], x[1], x[2]))
    return rows


def validate_rows(rows: Sequence[Tuple[str, dt.date, dt.date]], asof_dates: Optional[Sequence[dt.date]] = None) -> Dict[str, object]:
    by_code: Dict[str, List[Tuple[dt.date, dt.date]]] = {}
    for code, start, stop in rows:
        if start > stop:
            raise RebuildError(f"发现非法区间：{code}\t{start}\t{stop}")
        by_code.setdefault(code, []).append((start, stop))

    overlap_errors: List[str] = []
    for code, spans in by_code.items():
        spans = sorted(spans)
        for i in range(1, len(spans)):
            prev = spans[i - 1]
            cur = spans[i]
            if cur[0] <= prev[1]:
                overlap_errors.append(f"{code}: {prev} overlaps {cur}")
    if overlap_errors:
        raise RebuildError("发现重叠区间：\n" + "\n".join(overlap_errors[:50]))

    if asof_dates is None:
        asof_dates = []

    counts = {}
    for d in asof_dates:
        n = sum(1 for _, s, e in rows if s <= d <= e)
        counts[str(d)] = n

    return {
        "codes": len(by_code),
        "rows": len(rows),
        "asof_counts": counts,
    }


def write_rows(rows: Sequence[Tuple[str, dt.date, dt.date]], out: Path) -> None:
    with out.open("w", encoding="utf-8") as f:
        for code, start, stop in rows:
            f.write(f"{code}\t{start.isoformat()}\t{stop.isoformat()}\n")


def write_audit(parsed_events: Sequence[ParsedEvent], audit_dir: Path) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)

    summary_path = audit_dir / "event_summary.csv"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "effective_date",
            "kind",
            "title",
            "pdf_url",
            "parser_used",
            "n_removed",
            "n_added",
            "removed_codes",
            "added_codes",
        ])
        for pe in parsed_events:
            writer.writerow([
                pe.event.effective_date.isoformat(),
                pe.event.kind,
                pe.event.title,
                pe.event.pdf_url,
                pe.parser_used,
                len(pe.removed),
                len(pe.added),
                " ".join(pe.removed),
                " ".join(pe.added),
            ])

    previews_dir = audit_dir / "previews"
    previews_dir.mkdir(exist_ok=True)
    for i, pe in enumerate(parsed_events, start=1):
        p = previews_dir / f"{i:03d}_{pe.event.effective_date.isoformat()}.txt"
        p.write_text(
            json.dumps(
                {
                    "event": asdict(pe.event),
                    "parser_used": pe.parser_used,
                    "removed": pe.removed,
                    "added": pe.added,
                    "raw_text_preview": pe.raw_text_preview,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )


MANIFEST_TEMPLATE = """effective_date,pdf_url,kind,title
2005-06-13,https://example.com/official_notice_200506.pdf,periodic,2005年6月定期调整
2005-12-12,https://example.com/official_notice_200512.pdf,periodic,2005年12月定期调整
2006-03-01,https://example.com/official_temp_notice_200603.pdf,temporary,2006年某次临时调整
"""


def write_manifest_template(path: Path) -> None:
    path.write_text(MANIFEST_TEMPLATE, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="从中证指数官网公告全量回放重建沪深300历史成分")
    ap.add_argument("--seed", type=Path, help="官方初始样本文件（2005-04-08）")
    ap.add_argument("--manifest", type=Path, help="官方公告清单 CSV")
    ap.add_argument("--out", type=Path, default=Path("csi300.strict.txt"), help="输出 instruments 文件")
    ap.add_argument("--end-date", default=DEFAULT_END_DATE.isoformat(), help="开放区间终止日，默认 2099-12-31")
    ap.add_argument(
        "--infer-periodic-effective-date",
        action="store_true",
        help="当 manifest 中 periodic 行未填 effective_date 时，按 6/12 月第二个星期五后的下一交易日自动推断",
    )
    ap.add_argument("--audit-dir", type=Path, help="输出审计目录（解析摘要、文本预览）")
    ap.add_argument(
        "--write-manifest-template",
        type=Path,
        help="仅生成 manifest 模板 CSV，然后退出",
    )
    args = ap.parse_args()

    if args.write_manifest_template:
        write_manifest_template(args.write_manifest_template)
        print(f"模板已写入: {args.write_manifest_template}")
        return

    if not args.seed or not args.manifest:
        ap.error("正常重建模式下，--seed 和 --manifest 都是必填项")

    end_date = parse_date(args.end_date)
    seed_codes = read_seed_codes(args.seed)
    if len(seed_codes) != 300:
        raise RebuildError(
            f"初始样本识别到 {len(seed_codes)} 只，不等于 300。请确认你提供的是 2005-04-08 官方初始样本。"
        )

    events = load_manifest(args.manifest, infer_periodic_effective_date=args.infer_periodic_effective_date)
    log(f"读取到 {len(events)} 个官方事件")

    parsed_events: List[ParsedEvent] = []
    for idx, event in enumerate(events, start=1):
        log(f"[{idx}/{len(events)}] 下载并解析: {event.effective_date} {event.kind} {event.pdf_url}")
        pdf_bytes = fetch_bytes(event.pdf_url)
        pe = parse_event_from_pdf(event, pdf_bytes)
        if len(pe.added) != len(pe.removed):
            raise RebuildError(
                f"事件 {event.effective_date} 解析得到调入/调出数量不一致：{len(pe.added)} vs {len(pe.removed)}\n"
                f"公告：{event.pdf_url}"
            )
        parsed_events.append(pe)

    rows = replay_history(seed_codes, parsed_events, end_date=end_date)
    report = validate_rows(rows, asof_dates=[START_DATE, dt.date.today()])
    write_rows(rows, args.out)
    log(f"已输出: {args.out}")
    log(f"校验摘要: {json.dumps(report, ensure_ascii=False)}")

    if args.audit_dir:
        write_audit(parsed_events, args.audit_dir)
        log(f"审计输出已写入: {args.audit_dir}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
