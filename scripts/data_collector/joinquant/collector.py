import os
import fire
import pandas as pd
from datetime import datetime, timedelta
from loguru import logger
from pathlib import Path

try:
    import jqdatasdk as jq
except ImportError:
    logger.error("Please install jqdatasdk first: pip install jqdatasdk")
    exit(1)

class JQCollector:
    """JoinQuant (聚宽) Data Collector for Qlib"""

    def __init__(self, username: str = None, password: str = None):
        if username and password:
            jq.auth(username, password)
        elif 'JQ_USERNAME' in os.environ and 'JQ_PASSWORD' in os.environ:
            jq.auth(os.environ['JQ_USERNAME'], os.environ['JQ_PASSWORD'])
        else:
            logger.warning("JoinQuant credentials not provided. Please use jq.auth() or set JQ_USERNAME/JQ_PASSWORD env vars.")

    def download_data(
        self,
        source_dir: str,
        start_date: str = "2020-01-01",
        end_date: str = None,
        symbols: list = None,
        frequency: str = "1m",
        limit_threshold: int = 950000,
    ):
        """
        Download minute data from JoinQuant.
        
        :param source_dir: Directory to save CSV files.
        :param start_date: Start date (YYYY-MM-DD).
        :param end_date: End date (YYYY-MM-DD), default is today.
        :param symbols: List of symbols (e.g., ['600000.XSHG']). If None, uses CSI300.
        :param frequency: Data frequency ('1m', '5m', '1d', etc.).
        :param limit_threshold: Daily data point limit protective threshold.
        """
        source_dir = Path(source_dir).expanduser().resolve()
        source_dir.mkdir(parents=True, exist_ok=True)

        if end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        
        if symbols is None:
            # Default to CSI300 constituents if no symbols provided
            symbols = jq.get_index_stocks('000300.XSHG')
            logger.info(f"Using CSI300 constituents: {len(symbols)} stocks")

        # Check remaining data points
        try:
            rem_points = jq.get_query_count()['spare']
            logger.info(f"Remaining JoinQuant data points: {rem_points}")
        except Exception as e:
            logger.error(f"Failed to check JoinQuant count: {e}. Ensure you are authenticated.")
            return

        total_downloaded_points = 0

        for symbol in symbols:
            file_path = source_dir / f"{symbol.lower()}.csv"
            
            # Simple resume logic: if file exists, we might want to append or skip
            # For 1min data, we usually download in one go for a range, 
            # or users can manage segments manually.
            if file_path.exists():
                logger.debug(f"File {file_path.name} already exists, skipping. Delete it to re-download.")
                continue

            if total_downloaded_points > limit_threshold:
                logger.warning("Reached daily safety limit threshold. Stopping for today.")
                break

            try:
                logger.info(f"Downloading {symbol} from {start_date} to {end_date}...")
                df = jq.get_price(
                    symbol, 
                    start_date=start_date, 
                    end_date=end_date, 
                    frequency=frequency,
                    fields=['open', 'close', 'high', 'low', 'volume', 'money', 'factor']
                )

                if df is None or df.empty:
                    logger.warning(f"No data found for {symbol}")
                    continue

                # Prepare for Qlib format
                df.index.name = 'date'
                df['symbol'] = symbol
                
                # Save to CSV
                df.to_csv(file_path)
                
                points_count = len(df)
                total_downloaded_points += points_count
                logger.success(f"Saved {symbol} ({points_count} points). Total today: {total_downloaded_points}")

            except Exception as e:
                logger.error(f"Failed to download {symbol}: {e}")
                if "超出每日最多重试次数" in str(e) or "limit" in str(e).lower():
                    break

        logger.info(f"Finished. Total downloaded points: {total_downloaded_points}")

if __name__ == "__main__":
    fire.Fire(JQCollector)
