"""
indicators.py — คำนวณ technical indicators ทั้งหมดด้วย pandas (ไม่ใช้ pandas-ta)
รองรับ: EMA, MACD, RSI, Stochastic, ATR, Bollinger Bands, Volume SMA
ใช้ pandas ล้วนๆ เพราะ pandas-ta ไม่รองรับ Python 3.14
"""

import pandas as pd
import numpy as np
from loguru import logger


class Indicators:
    """
    คำนวณ indicators ทั้งหมดลงใน DataFrame
    รับ DataFrame จาก DataFetcher แล้วเพิ่ม columns ใหม่กลับไป
    """

    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        เพิ่ม indicator columns ทั้งหมดลงใน df แล้วคืนกลับ
        ต้องมี columns: open, high, low, close, volume
        """
        if df.empty or len(df) < 50:
            logger.warning(f"DataFrame มีข้อมูลน้อยเกินไป: {len(df)} rows")
            return df

        try:
            df = self._add_trend(df)
            df = self._add_momentum(df)
            df = self._add_volatility(df)
            df = self._add_volume(df)
            logger.debug(f"คำนวณ indicators ครบ: {len(df.columns)} columns")
        except Exception as e:
            logger.error(f"calculate_all error: {e}")

        return df

    def _ema(self, series: pd.Series, length: int) -> pd.Series:
        """คำนวณ Exponential Moving Average"""
        return series.ewm(span=length, adjust=False).mean()

    def _sma(self, series: pd.Series, length: int) -> pd.Series:
        """คำนวณ Simple Moving Average"""
        return series.rolling(window=length).mean()

    def _add_trend(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Trend indicators: EMA 20/50/200 และ MACD (12, 26, 9)
        """
        df["EMA_20"] = self._ema(df["close"], 20)
        df["EMA_50"] = self._ema(df["close"], 50)
        df["EMA_200"] = self._ema(df["close"], 200)

        # MACD
        ema_fast = self._ema(df["close"], 12)
        ema_slow = self._ema(df["close"], 26)
        df["MACD"] = ema_fast - ema_slow
        df["MACD_signal"] = self._ema(df["MACD"], 9)
        df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

        return df

    def _add_momentum(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Momentum indicators: RSI 14 และ Stochastic (14, 3, 3)
        """
        # RSI — คำนวณด้วย Wilder's smoothing method
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["RSI_14"] = 100 - (100 / (1 + rs))

        # Stochastic (14, 3)
        low_min = df["low"].rolling(window=14).min()
        high_max = df["high"].rolling(window=14).max()
        diff = high_max - low_min
        diff = diff.replace(0, np.nan)
        df["stoch_k"] = 100 * (df["close"] - low_min) / diff
        df["stoch_d"] = df["stoch_k"].rolling(window=3).mean()

        return df

    def _add_volatility(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Volatility: ATR 14 และ Bollinger Bands (20, 2)
        """
        # ATR — Average True Range
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["ATR_14"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()

        # Bollinger Bands (20 period, 2 std dev)
        sma20 = self._sma(df["close"], 20)
        std20 = df["close"].rolling(window=20).std()
        df["BB_middle"] = sma20
        df["BB_upper"] = sma20 + 2 * std20
        df["BB_lower"] = sma20 - 2 * std20

        return df

    def _add_volume(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Volume: Volume SMA 20 และ Volume ratio
        """
        df["Volume_SMA_20"] = self._sma(df["volume"], 20)
        # ป้องกัน division by zero
        df["Volume_ratio"] = df["volume"] / df["Volume_SMA_20"].replace(0, np.nan)

        return df

    def get_latest(self, df: pd.DataFrame) -> dict:
        """
        คืน dict ของ indicator ล่าสุด (แถวสุดท้าย)
        ใช้ใน agent เพื่อดึงค่า indicator ง่ายๆ
        """
        if df.empty:
            return {}
        latest = df.iloc[-1].to_dict()
        return {k: v for k, v in latest.items() if pd.notna(v)}
