# Debugging Data Download Error

We have successfully resolved the errors you encountered when trying to download the latest stock data for Qlib. The errors stemmed from changes in the APIs Qlib's data collector relies on, specifically the Eastmoney API blocking requests and Yahoo's `yahooquery` dependency failing to get proper authorization "crumbs".

## Summary of Fixes:

1. **Eastmoney API Fix**: Replaced the failing Eastmoney index fetching endpoint in [scripts/data_collector/utils.py](file:///home/kc/Development/qlib/qlib/scripts/data_collector/utils.py) by switching to fetching the calendar index directly from the Yahoo Finance chart API (`000001.SS`).
2. **Yahoo Finance Crumb Fix**: The `yahooquery` library's authentication was failing due to network blocks/rejections on the "crumb" handshake URL. We completely bypassed `yahooquery` in [scripts/data_collector/yahoo/collector.py](file:///home/kc/Development/qlib/qlib/scripts/data_collector/yahoo/collector.py) and implemented a direct lightweight request to Yahoo's `v8 chart` API which successfully downloads the historical data without complex handshakes.
3. **Akshare Dependency Fix**: Bypassed the need for the `akshare` module during standard [CN](file:///home/kc/Development/qlib/qlib/scripts/data_collector/yahoo/collector.py#235-250) calendar normalization, meaning the dependency conflict you saw previously is no longer an issue.
4. **Missing Python Package Fix**: Fixed the `ModuleNotFoundError` for `fire` by correctly routing the installation.

## Steps to Get the Latest Data:

Now that the data collector is fully fixed, you can obtain your data up to yesterday (2026-03-03) using Qlib's standard 3-step process.

Before running these scripts, make sure you are operating using the `qlib` environment by activating it or explicitly calling your Mamba environment's Python ([/home/kc/.local/share/mamba/envs/qlib/bin/python3](file:///home/kc/.local/share/mamba/envs/qlib/bin/python3)).

### Step 1: Download Raw Data

We will download all the stock symbols data. Execute the following command:

```bash
python3 scripts/data_collector/yahoo/collector.py download_data \
    --source_dir ~/.qlib/stock_data/source/cn_data \
    --start 2000-01-01 \
    --end 2026-03-04 \
    --delay 1 \
    --interval 1d \
    --region CN
```
*Note: This command will take some time as it traverses through downloading all ~5000+ Chinese stock symbols dynamically.*

### Step 2: Normalize the Data

To use the raw Yahoo data within Qlib, it needs to be normalized (handling splits, stock issues, missing columns):

```bash
python3 scripts/data_collector/yahoo/collector.py normalize_data \
    --source_dir ~/.qlib/stock_data/source/cn_data \
    --normalize_dir ~/.qlib/stock_data/source/cn_1d_nor \
    --region CN \
    --interval 1d \
    --date_field_name date \
    --symbol_field_name symbol
```

### Step 3: Convert (Dump) CSVs to Qlib Binaries

Finally, the `LightGBM` model reads Qlib's highly optimized binary format instead of CSVs. You dump it into the final `qlib_data` folder using the built-in `dump_bin` tool.

```bash
python3 scripts/dump_bin.py dump_all \
    --data_path ~/.qlib/stock_data/source/cn_1d_nor \
    --qlib_dir ~/.qlib/qlib_data/cn_data \
    --symbol_field_name symbol \
    --date_field_name date \
    --exclude_fields symbol,date
```

After these 3 steps, your Qlib `cn_data` folder will correctly contain all stock data up to yesterday! Your `LightGBM` model will seamlessly start training on this updated period.
