# JoinQuant Data Collector for Qlib

This script allows you to download historical 1-minute (and other frequencies) A-share data from JoinQuant (聚宽) and save it in a format compatible with Qlib.

## Requirements

1.  **jqdatasdk**: Install the JoinQuant SDK.
    ```bash
    pip install jqdatasdk
    ```
2.  **JoinQuant Account**: You need a valid username and password from [JoinQuant](https://www.joinquant.com/).

## Usage

### 1. Simple Download

You can run the collector directly from the command line using `fire`.

```bash
# Set credentials as environment variables (recommended)
export JQ_USERNAME='your_username'
export JQ_PASSWORD='your_password'

# Download CSI300 1-minute data from 2024-01-01 to 2024-01-31
python collector.py download_data \
    --source_dir ~/.qlib/stock_data/source/cn_data_1min_jq \
    --start_date 2024-01-01 \
    --end_date 2024-01-31 \
    --frequency 1m
```

### 2. Parameters

- `--source_dir`: (Required) Where to save the generated CSV files.
- `--start_date`: (Default: "2020-01-01") Start date in YYYY-MM-DD format.
- `--end_date`: (Default: Today) End date in YYYY-MM-DD format.
- `--symbols`: (Optional) A list of symbols like `['600000.XSHG', '000001.XSHE']`. If not provided, it defaults to all CSI300 constituents.
- `--frequency`: (Default: '1m') Support '1m', '5m', '15m', '30m', '60m', '1d'.
- `--limit_threshold`: (Default: 950,000) Safety buffer for the JoinQuant daily 1,000,000 data point limit.

### 3. Segmented Download (Daily Limit Handling)

Since JoinQuant limits free accounts to 1 million data points per day, you should download in chunks:

1.  **Day 1**: Run for 50 stocks for 1 year.
2.  **Day 2**: The script automatically skips existing files. Run it again for the next batch or the same symbols with a different date range.

## Integration with Qlib

After downloading the CSVs, follow the standard Qlib pipeline:

1.  **Normalize**:
    ```bash
    python ../yahoo/collector.py normalize_data --qlib_data_1d_dir ~/.qlib/qlib_data/cn_data --source_dir ~/.qlib/stock_data/source/cn_data_1min_jq --normalize_dir ~/.qlib/stock_data/source/cn_1min_nor_jq --region CN --interval 1min
    ```
2.  **Dump Bin**:
    ```bash
    python ../../dump_bin.py dump_all --data_path ~/.qlib/stock_data/source/cn_1min_nor_jq --qlib_dir ~/.qlib/qlib_data/cn_data_1min_jq --freq 1min --exclude_fields date,symbol --file_suffix .csv
    ```
