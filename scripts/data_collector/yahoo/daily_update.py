import datetime
import sys
from pathlib import Path
import fire
from loguru import logger
import pandas as pd

CUR_DIR = Path(__file__).resolve().parent
if str(CUR_DIR) not in sys.path:
    sys.path.insert(0, str(CUR_DIR))
if str(CUR_DIR.parent.parent) not in sys.path:
    sys.path.append(str(CUR_DIR.parent.parent))

from data_collector.yahoo.collector import Run

def update_daily_data(
    source_dir: str = "~/.qlib/stock_data/source/cn_data",
    qlib_data_dir: str = "~/.qlib/qlib_data/cn_data",
    normalize_dir: str = "~/.qlib/stock_data/normalize_cn_data",
    region: str = "CN",
    delay: float = 0.1,
    max_workers: int = 16,
):
    """
    Downloads missing stock data up to the current date from Yahoo Finance,
    appends to source CSVs, and seamlessly updates the Qlib binary formats and instruments.

    Args:
        source_dir: The directory containing existing downloaded CSVs (e.g. cn_data)
        qlib_data_dir: The directory containing existing dumped Qlib binary files
        normalize_dir: Temporary directory used to store normalized increments
        region: The market region (e.g. "CN", "US", "BR", "IN")
        delay: The sleep delay between API requests
        max_workers: Number of concurrent threads for downloading
    """
    source_dir = str(Path(source_dir).expanduser().resolve())
    qlib_data_dir = str(Path(qlib_data_dir).expanduser().resolve())
    normalize_dir = str(Path(normalize_dir).expanduser().resolve())

    logger.info("Starting automated daily data update from Yahoo...")
    logger.info(f"Using Source Directory: {source_dir}")
    logger.info(f"Using Qlib Bin Directory: {qlib_data_dir}")
    logger.info(f"Using Normalize Temp Directory: {normalize_dir}")
    logger.info(f"Market Region: {region}")
    logger.info(f"Concurrency: {max_workers} workers, {delay}s delay")

    run = Run(
        source_dir=source_dir,
        normalize_dir=normalize_dir,
        max_workers=max_workers,
        interval="1d",
        region=region
    )
    
    # Read the latest trading date from Qlib binary calendar
    calendar_path = Path(qlib_data_dir) / "calendars" / "day.txt"
    if calendar_path.exists():
        with open(calendar_path, "r") as f:
            lines = f.read().strip().split("\n")
            if lines and lines[-1]:
                # The next date to fetch from is the day after the last recorded date
                last_date = pd.Timestamp(lines[-1])
                start_date = (last_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                logger.info(f"Latest data found on {last_date.strftime('%Y-%m-%d')}. Fetching from {start_date}...")
            else:
                start_date = "2000-01-01"
    else:
        logger.warning(f"Calendar file not found at {calendar_path}. Fetching from default start date 2000-01-01.")
        start_date = "2000-01-01"

    end_date = datetime.datetime.now().strftime("%Y-%m-%d")

    # If the start date is strictly greater than today's date, there's nothing to download
    if pd.Timestamp(start_date) > pd.Timestamp(end_date):
        logger.info(f"Data is already up to date (start_date {start_date} is after {end_date}). Exiting.")
        return

    # Executes the pipeline.
    # Note: `update_data_to_bin` in Qlib's Yahoo collector internally reads the calendar,
    # but we can pass `trading_date` explicitly as `start_date` by overriding standard methods
    # or just trust the script now that we ensured the date bounds.
    # We'll monkey-patch/inject the start_date into `update_data_to_bin` if necessary, 
    # but Qlib's run.update_data_to_bin actually computes trading_date internally by reading day.txt!
    # Let's verify and override its start parameter by modifying how download_data is called.
    
    # We can invoke download_data directly then normalize and dump ourselves to be safely explicit:
    logger.info("Downloading data...")
    run.download_data(delay=delay, start=start_date, end=end_date)
    
    logger.info("Normalizing data...")
    run.normalize_data_1d_extend(old_qlib_data_dir=qlib_data_dir)
    
    logger.info("Dumping features to bin...")
    from dump_bin import DumpDataUpdate
    dump_update = DumpDataUpdate(
        data_path=run.normalize_dir,
        qlib_dir=qlib_data_dir,
        exclude_fields="symbol,date",
        max_workers=run.max_workers,
    )
    dump_update.dump()
    
    # Update instruments
    # DO NOT update instruments for now, these operations need to be done manually
    # logger.info("Updating indices (instruments)...")
    # region_lower = region.lower()
    # if region_lower in ["cn", "us"]:
    #     import importlib
    #     index_list = ["CSI100", "CSI300"] if region_lower == "cn" else ["SP500", "NASDAQ100", "DJIA", "SP400"]
    #     try:
    #         get_instruments = getattr(
    #             importlib.import_module(f"data_collector.{region_lower}_index.collector"), "get_instruments"
    #         )
    #         for _index in index_list:
    #             get_instruments(str(qlib_data_dir), _index, market_index=f"{region_lower}_index")
    #     except Exception as e:
    #         logger.warning(f"Failed to update indices: {e}")
    
    logger.info(f"[{datetime.datetime.now()}] Daily update successfully completed.")

if __name__ == "__main__":
    fire.Fire(update_daily_data)
