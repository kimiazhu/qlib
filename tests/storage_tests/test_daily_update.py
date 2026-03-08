import unittest
import pandas as pd
import numpy as np
import shutil
from pathlib import Path
from unittest.mock import patch

import sys
# make sure the scripts dir is available to import
CUR_DIR = Path(__file__).resolve().parent
sys.path.append(str(CUR_DIR.parent.parent / "scripts"))
from data_collector.yahoo.daily_update import update_daily_data
from dump_bin import DumpDataAll

class TestDailyUpdate(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(__file__).parent / "test_daily_update_data"
        self.source_dir = self.test_dir / "source"
        self.qlib_dir = self.test_dir / "qlib"
        self.normalize_dir = self.test_dir / "normalize"
        
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.source_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Create initial dummy data for 2 days
        self.symbol = "sh600000"
        self.dates = ["2026-03-03", "2026-03-04"]
        
        df = pd.DataFrame({
            "date": pd.to_datetime(self.dates),
            "open": [10.0, 10.5],
            "high": [10.2, 10.8],
            "low": [9.8, 10.4],
            "close": [10.1, 10.7],
            "volume": [1000, 1500],
            "adjclose": [10.1, 10.7],
            "factor": [1.0, 1.0],
            "change": [0.0, (10.7/10.1 - 1)],
            "symbol": [self.symbol, self.symbol]
        })
        self.source_csv = self.source_dir / f"{self.symbol}.csv"
        df.to_csv(self.source_csv, index=False)
        
        # 2. Dump this initial data to qlib bin
        dump_all = DumpDataAll(
            data_path=str(self.source_dir),
            qlib_dir=str(self.qlib_dir),
            freq="day",
            exclude_fields="symbol,date",
            max_workers=1
        )
        dump_all.dump()
        
    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)

    @patch("collector.YahooCollector.get_data_from_remote")
    def test_daily_update(self, mock_get_data):
        # Setup mock for the new appended day
        appended_date = "2026-03-05"
        mock_df = pd.DataFrame({
            "symbol": [self.symbol.upper()],
            "date": [appended_date],
            "open": [10.6],
            "high": [11.0],
            "low": [10.5],
            "close": [10.9],
            "volume": [2000],
            "adjclose": [10.9]
        })
        mock_get_data.return_value = mock_df
        
        # Provide standard get_instrument_list wrapper just to return our single stock
        with patch("collector.get_hs_stock_symbols") as mock_instruments:
            mock_instruments.return_value = ["600000.ss"]
            with patch("data_collector.cn_index.collector.get_instruments") as mock_cn_index:
                # Mock component downloads so it doesn't fail on index fetching
                mock_cn_index.return_value = None
            
                # 3. Call update_daily_data
                update_daily_data(
            source_dir=str(self.source_dir),
            qlib_data_dir=str(self.qlib_dir),
            normalize_dir=str(self.normalize_dir),
            region="CN",
            max_workers=1,
            delay=0,
        )    
        # 4. Verify the source CSV has 3 days
        updated_csv_df = pd.read_csv(self.source_csv)
        print("CSV CONTENTS:")
        print(updated_csv_df)
        self.assertEqual(len(updated_csv_df), 3)
        self.assertEqual(updated_csv_df["date"].iloc[-1], appended_date)
        
        # 5. Verify Qlib binary
        # Features should have the new row
        bin_path = self.qlib_dir / "features" / "sh600000" / "close.day.bin"
        features = np.fromfile(bin_path, dtype="<f")
        # Format of qlib 1D bin is:
        # np.hstack([date_index, _df[field]]).astype("<f").tofile(bin_path)
        # For update: np.array(_df[field]).astype("<f").tofile(fp)
        # So len is 1 (start_index) + 3 (values) = 4
        self.assertEqual(len(features), 4)
        self.assertAlmostEqual(features[-1], 10.9, places=1)
        
        # Calendar should have updated
        cal_path = self.qlib_dir / "calendars" / "day.txt"
        with open(cal_path, "r") as f:
            cal_lines = f.read().strip().split("\n")
        self.assertEqual(len(cal_lines), 3)
        self.assertEqual(cal_lines[-1], appended_date)
        
        # Instruments should have updated
        inst_path = self.qlib_dir / "instruments" / "all.txt"
        with open(inst_path, "r") as f:
            inst_lines = f.read().strip().split("\n")
        self.assertTrue("2026-03-05" in inst_lines[0])

if __name__ == "__main__":
    unittest.main()
