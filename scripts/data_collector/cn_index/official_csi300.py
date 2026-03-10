#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import re
import sys
import json
import time
import random
import argparse
import urllib.parse
from pathlib import Path
from typing import List, Dict, Set, Tuple, Optional
import requests
import pandas as pd
from tqdm import tqdm
from loguru import logger
from io import BytesIO

# Default configuration
DEFAULT_START_DATE = "2005-01-01"
DEFAULT_END_DATE = pd.Timestamp.today().strftime("%Y-%m-%d")
DEFAULT_FAR_END_DATE = "2099-12-31"

# CSI API Endpoints
SEARCH_API = "https://www.csindex.com.cn/csindex-home/search/search-content"
NEWS_DETAIL_API = "https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementById"
LATEST_CONS_URL = "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/000300cons.xls"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
]

def get_headers():
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.csindex.com.cn/",
        "Origin": "https://www.csindex.com.cn",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    return headers

def retry_request(url: str, method: str = "get", payload: Dict = None, max_retry: int = 5, sleep: float = 2.0):
    for i in range(max_retry):
        try:
            headers = get_headers()
            if method.lower() == "post":
                resp = requests.post(url, headers=headers, json=payload, timeout=30)
            else:
                resp = requests.get(url, headers=headers, params=payload, timeout=30)
            
            if resp.status_code == 200:
                # logger.debug(f"Response from {url}: {resp.text[:500]}...")
                return resp
            logger.warning(f"Request failed with status {resp.status_code} for {url}, retrying ({i+1}/{max_retry})...")
        except Exception as e:
            logger.warning(f"Request error: {e}, retrying ({i+1}/{max_retry})...")
        
        # Exponential backoff with jitter
        wait_time = sleep * (2 ** i) + random.random() * 2
        time.sleep(wait_time)
    
    raise RuntimeError(f"Failed to fetch {url} after {max_retry} retries")

class OfficialCSI300Collector:
    def __init__(self, qlib_dir: Path, cache_dir: Optional[Path] = None):
        self.qlib_dir = qlib_dir
        self.instruments_dir = qlib_dir / "instruments"
        self.instruments_dir.mkdir(parents=True, exist_ok=True)
        
        # Cache directory for Excel files
        if cache_dir is None:
            self.cache_dir = qlib_dir / "csi300_official_cache"
        else:
            self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.index_code = "000300"
        self.index_name = "csi300"
        self.output_file = self.instruments_dir / "csi300.txt"

    def normalize_symbol(self, symbol: str) -> str:
        """Normalize stock code to SHXXXXXX or SZXXXXXX"""
        if isinstance(symbol, float):
            symbol = str(int(symbol))
        symbol = str(symbol).strip().zfill(6)
        if symbol.startswith("60") or symbol.startswith("688") or symbol.startswith("900"):
            return f"SH{symbol}"
        else:
            return f"SZ{symbol}"

    def get_announcements(self) -> List[Dict]:
        """Fetch all announcements related to CSI 300 adjustments"""
        logger.info("Fetching announcement list from CSI website...")
        announcements = []
        page = 1
        rows = 50 # Fetch more per page
        
        while True:
            # Try a broader search query and more typical parameters
            params = {
                "lang": "cn",
                "searchInput": "沪深300",
                "pageNum": str(page),
                "pageSize": str(rows),
                "sortField": "date",
                "dateRange": "all",
                "contentType": "announcement"
            }
            # The search API might actually prefer GET with these params as seen in browser network
            # logger.info(f"Searching page {page} with query '沪深300'...")
            resp = retry_request(SEARCH_API, method="get", payload=params)
            data = resp.json()
            
            logger.debug(f"Search API Info - Code: {data.get('code')}, Total: {data.get('total')}")
            
            items = data.get("data", [])
            if not items:
                logger.debug(f"No results found on page {page}. Raw data: {data}")
                break
            
            for item in items:
                headline = item.get("headline", "")
                item_date = item.get("itemDate", "")
                ann_id = item.get("id")
                # Filtering for adjustment announcements
                if "沪深300" in headline and ("样本" in headline or "调整" in headline or "名单" in headline):
                    announcements.append({
                        "id": ann_id,
                        "headline": headline,
                        "date": item_date
                    })
            
            total = data.get("total", 0)
            if page * rows >= total:
                break
            page += 1
            
        logger.info(f"Found {len(announcements)} relevant announcements.")
        return announcements

    def download_attachment(self, attachment_info: Dict) -> Optional[Path]:
        """Download and cache an attachment (Excel)"""
        file_url = attachment_info.get("fileUrl")
        file_name = attachment_info.get("fileName", "attachment.xls")
        if not file_url:
            return None
            
        if not file_url.startswith("http"):
            file_url = f"https://www.csindex.com.cn{file_url}"
            
        # Use a safe filename
        safe_name = re.sub(r'[\\/*?:"<>|]', "_", file_name)
        file_path = self.cache_dir / safe_name
        
        if file_path.exists():
            # logger.debug(f"Using cached file: {file_path}")
            return file_path
            
        logger.info(f"Downloading: {file_name} from {file_url}")
        resp = retry_request(file_url)
        with open(file_path, "wb") as f:
            f.write(resp.content)
            
        return file_path

    def parse_excel_adjustment(self, file_path: Path, title: str) -> List[Dict]:
        """Parse '调入' and '调出' sheets from the historical adjustment Excel"""
        changes = []
        try:
            # Try to infer dates from title or content
            # Most titles look like "关于沪深300等指数样本定期调整的公告"
            # We need the effective date. Usually contained in the announcement body.
            # For now, let's assume we'll get the date later from the announcement metadata if needed.
            
            df_map = pd.read_excel(file_path, sheet_name=None)
            
            # CSI 300 usually uses index code "000300" in the Excel
            for sheet_name in df_map:
                if "调入" in sheet_name:
                    change_type = "add"
                elif "调出" in sheet_name:
                    change_type = "remove"
                else:
                    continue
                
                df = df_map[sheet_name]
                # Look for columns like "证券代码" or "Stock Code" 및 "指数代码" or "Index Code"
                code_col = None
                index_col = None
                for col in df.columns:
                    if "证券代码" in str(col) or "Stock Code" in str(col):
                        code_col = col
                    if "指数代码" in str(col) or "Index Code" in str(col):
                        index_col = col
                
                if code_col is None:
                    continue
                
                # Filter for CSI 300
                if index_col:
                    mask = (df[index_col].astype(str).str.contains("000300")) | (df[index_col] == 300)
                    df_filtered = df[mask]
                else:
                    # If no index code column, might be just the CSI 300 sheet?
                    df_filtered = df
                
                for _, row in df_filtered.iterrows():
                    symbol = self.normalize_symbol(row[code_col])
                    changes.append({"symbol": symbol, "type": change_type})
                    
        except Exception as e:
            logger.error(f"Error parsing {file_path}: {e}")
            
        return changes

    def get_announcement_detail(self, announcement_id: str) -> Dict:
        """Fetch announcement detail to get effective date and attachments"""
        resp = retry_request(NEWS_DETAIL_API, payload={"id": announcement_id})
        return resp.json().get("data", {})

    def extract_effective_date(self, text: str, publish_date: str) -> str:
        """Extract effective date from announcement text, fallback to publish date"""
        # Common pattern: "将于2023年6月12日正式生效"
        match = re.search(r"(\d{4})年(\d+)月(\d+)日", text)
        if match:
            return f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
        return publish_date[:10]

    def collect_all_changes(self) -> pd.DataFrame:
        """Main flow to collect all changes over time"""
        announcements = self.get_announcements()
        all_changes = []
        
        for ann in tqdm(announcements, desc="Processing announcements"):
            ann_id = ann.get("id")
            try:
                # Detail call is still needed for attachments, but let's be VERY careful
                detail = self.get_announcement_detail(ann_id)
                content = detail.get("content", "")
                publish_date = detail.get("publishDate", ann.get("date", ""))
                effective_date = self.extract_effective_date(content, publish_date)
                
                attachments = detail.get("enclosureList", [])
                for att in attachments:
                    fname = att.get("fileName", "").lower()
                    if (".xls" in fname or ".xlsx" in fname) and ("名单" in fname or "样本" in fname):
                        file_path = self.download_attachment(att)
                        if file_path:
                            changes = self.parse_excel_adjustment(file_path, ann.get("headline", ""))
                            for c in changes:
                                c["date"] = effective_date
                                all_changes.append(c)
            except Exception as e:
                logger.error(f"Failed to process announcement {ann_id}: {e}")
            
            # Very conservative rate limiting
            time.sleep(5.0 + random.random() * 5)
        
        if not all_changes:
            logger.warning("No changes found in history announcements.")
            return pd.DataFrame()
            
        df = pd.DataFrame(all_changes)
        return df

    def get_latest_constituents(self) -> Set[str]:
        """Download the latest constituent list as the 'base' or 'current' state"""
        logger.info("Downloading latest constituent list...")
        file_path = self.download_attachment({"fileUrl": LATEST_CONS_URL, "fileName": "000300cons_latest.xls"})
        if not file_path:
            return set()
            
        df = pd.read_excel(file_path)
        # Usually has columns "指数代码", "成分券代码"
        code_col = None
        for col in df.columns:
            if "成分券代码" in str(col) or "Stock Code" in str(col):
                code_col = col
                break
        
        if code_col:
            return {self.normalize_symbol(x) for x in df[code_col]}
        return set()

    def reconstruct_history(self, changes_df: pd.DataFrame, current_constituents: Set[str]):
        """Reconstruct the membership spans by working backward from the current state"""
        if changes_df.empty:
            logger.error("No changes to reconstruct history.")
            return
            
        # Group by date
        changes_df["date"] = pd.to_datetime(changes_df["date"])
        
        # We work BACKWARD from the current state
        # current_constituents is the state as of today
        # unique() returns numpy datetime64, so we convert them to pd.Timestamp
        all_dates = [pd.Timestamp(d) for d in sorted(changes_df["date"].unique(), reverse=True)]
        
        # active_spans: symbol -> (start_date, end_date)
        # For current constituents, they are active from 'some_date' to 2099-12-31
        current_state = set(current_constituents)
        history_spans = [] # List of (symbol, start_date, end_date)
        
        latest_date = pd.Timestamp.today().strftime("%Y-%m-%d")
        
        # active_elements: symbol -> end_date
        active_elements = {s: DEFAULT_FAR_END_DATE for s in current_state}
        
        for date in all_dates:
            date_str = date.strftime("%Y-%m-%d")
            day_changes = changes_df[changes_df["date"] == date]
            
            adds = set(day_changes[day_changes["type"] == "add"]["symbol"])
            removes = set(day_changes[day_changes["type"] == "remove"]["symbol"])
            
            # If a stock was 'added' on this date, it means it was NOT in the index before this date.
            # So its span ends here (start_date = current_date, end_date remains as is)
            for s in adds:
                if s in active_elements:
                    end_date = active_elements.pop(s)
                    history_spans.append((s, date_str, end_date))
            
            # If a stock was 'removed' on this date, it means it WAS in the index before this date.
            # Its span that ended BEFORE this date is finished, and we start a NEW span for it
            # that ends at the day before this adjustment.
            for s in removes:
                prev_end_date = (date - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                active_elements[s] = prev_end_date
                
        # Any remaining active_elements started at the beginning of our history
        for s, end_date in active_elements.items():
            history_spans.append((s, "2005-01-01", end_date))
            
        output_df = pd.DataFrame(history_spans, columns=["symbol", "start_date", "end_date"])
        output_df = output_df.sort_values(["symbol", "start_date"])
        output_df.to_csv(self.output_file, sep="\t", index=False, header=False)
        logger.info(f"Reconstructed {len(output_df)} membership spans and saved to {self.output_file}")

    def run(self):
        changes_df = self.collect_all_changes()
        current_stocks = self.get_latest_constituents()
        self.reconstruct_history(changes_df, current_stocks)

def main():
    parser = argparse.ArgumentParser(description="Download CSI300 historical constituents from official CSI website.")
    parser.add_argument("--qlib_dir", required=True, help="Qlib data directory, e.g. ~/.qlib/qlib_data/cn_data")
    parser.add_argument("--cache_dir", help="Directory to store downloaded Excel files.")
    args = parser.parse_args()
    
    qlib_dir = Path(args.qlib_dir).expanduser().resolve()
    cache_dir = Path(args.cache_dir).expanduser().resolve() if args.cache_dir else None
    
    collector = OfficialCSI300Collector(qlib_dir, cache_dir)
    collector.run()

if __name__ == "__main__":
    main()
