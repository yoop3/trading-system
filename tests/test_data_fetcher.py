"""
test_data_fetcher.py — ทดสอบ DataFetcher และ Indicators
ไม่ต้องการ API key สำหรับ test indicator calculation
"""

import unittest
import pandas as pd
import numpy as np
from core.indicators import Indicators


class TestIndicators(unittest.TestCase):
    def setUp(self):
        """สร้าง DataFrame ตัวอย่างสำหรับทดสอบ"""
        self.ind = Indicators()
        # สร้างข้อมูล OHLCV จำลอง 200 แท่ง
        np.random.seed(42)
        n = 200
        close = 2000.0 + np.cumsum(np.random.randn(n) * 10)
        self.df = pd.DataFrame({
            "open":   close - np.random.uniform(0, 20, n),
            "high":   close + np.random.uniform(0, 20, n),
            "low":    close - np.random.uniform(0, 20, n),
            "close":  close,
            "volume": np.random.uniform(100, 1000, n),
        })

    def test_calculate_all(self):
        df = self.ind.calculate_all(self.df)
        # ตรวจว่ามี columns ที่ต้องการ
        required = ["EMA_20", "EMA_50", "EMA_200", "MACD", "RSI_14",
                    "ATR_14", "BB_upper", "BB_lower", "Volume_SMA_20"]
        for col in required:
            self.assertIn(col, df.columns, f"Missing column: {col}")

    def test_rsi_range(self):
        df = self.ind.calculate_all(self.df)
        rsi = df["RSI_14"].dropna()
        self.assertTrue((rsi >= 0).all() and (rsi <= 100).all(), "RSI ต้องอยู่ 0-100")

    def test_get_latest(self):
        df = self.ind.calculate_all(self.df)
        latest = self.ind.get_latest(df)
        self.assertIsInstance(latest, dict)
        self.assertIn("close", latest)
        self.assertIn("RSI_14", latest)

    def test_empty_df(self):
        empty_df = pd.DataFrame()
        result = self.ind.calculate_all(empty_df)
        self.assertTrue(result.empty)

    def test_data_fetcher_import(self):
        from core.data_fetcher import DataFetcher
        df = DataFetcher()
        self.assertIsNotNone(df)


if __name__ == "__main__":
    unittest.main()
