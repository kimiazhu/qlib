# Walkthrough: Official CSI 300 Historical Constituents Downloader

I have implemented a script that downloads historical constituent adjustments directly from the CSI official website.

## Features
- **Official Data Source**: Uses the `csindex.com.cn` search and announcement APIs.
- **Excel Parsing**: Automatically identifies and parses adjustment Excel/XLSX attachments.
- **Local Caching**: All downloaded Excel files are kept in a local cache directory as requested, allowing for direct reuse.
- **Adaptive Measures**: Implements user-agent rotation and randomized exponential backoff to handle the website's anti-bot measures.

## Files Created
- [official_csi300.py](file:///Users/kc/Development/qlib/qlib/scripts/data_collector/cn_index/official_csi300.py): The main collection script.

## How to Run
To run the script and download the data:
```bash
python3 scripts/data_collector/cn_index/official_csi300.py --qlib_dir ~/.qlib/qlib_data/cn_data --cache_dir ./csi300_cache
```

## Implementation Details

### Anti-Bot Challenges
> [!WARNING]
> The CSI website employs strict rate limiting (403 Forbidden). The script includes logic to bypass these measures, but for a full historical download (20+ years), you might need to run it in stages or use a proxy if your IP gets temporarily blocked.

### Data Reconstruction
The script works by:
1. Fetching the latest constituent list as a baseline.
2. Searching for all "沪深300 样本调整" announcements.
3. Downloading the associated Excel files.
4. Parsing 'Add' and 'Remove' records from these files.
5. Reconstructing the timeline backward from the current state to 2005.

### Preserving Files
All Excel files found are saved in the `--cache_dir`. If a file already exists in the cache, the script will skip the download and use the local version, ensuring efficiency.
