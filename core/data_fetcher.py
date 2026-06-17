"""
data_fetcher.py — ดึงข้อมูลราคาและตลาดจาก Binance Futures
ใช้ ccxt library — รองรับทั้ง mainnet และ testnet
หมายเหตุ: keys ปัจจุบันเป็น mainnet keys (BINANCE_TESTNET=false)
"""

import os
import ccxt.async_support as ccxt
import pandas as pd
from loguru import logger
from typing import Optional


class DataFetcher:
    """
    เชื่อมต่อ Binance Futures ผ่าน ccxt
    ดึงราคา, OHLCV, balance, positions, funding rate, open interest
    """

    def __init__(self):
        self.symbol = os.getenv("TRADING_SYMBOL", "ETH/USDT:USDT")
        is_testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

        self.exchange = ccxt.binanceusdm({
            "apiKey": os.getenv("BINANCE_API_KEY", ""),
            "secret": os.getenv("BINANCE_API_SECRET", ""),
            "options": {
                "defaultType": "future",
                "adjustForTimeDifference": True,
            },
        })

        if is_testnet:
            # ถ้าใช้ testnet keys (จาก testnet.binancefuture.com) → ชี้ fapi ไปที่ testnet
            t = "https://testnet.binancefuture.com"
            for key, path in [
                ("fapiPublic",    "/fapi/v1"),
                ("fapiPublicV2",  "/fapi/v2"),
                ("fapiPublicV3",  "/fapi/v3"),
                ("fapiPrivate",   "/fapi/v1"),
                ("fapiPrivateV2", "/fapi/v2"),
                ("fapiPrivateV3", "/fapi/v3"),
                ("fapiData",      "/futures/data"),
            ]:
                self.exchange.urls["api"][key] = t + path
            logger.info(f"DataFetcher: Testnet mode → {t}")
        else:
            logger.info("DataFetcher: Mainnet mode (read+trade) — TRADING_ENABLED controls orders")

    async def close(self):
        """ปิด connection เมื่อเลิกใช้งาน"""
        await self.exchange.close()

    async def get_current_price(self, symbol: Optional[str] = None) -> float:
        """
        ดึงราคาปัจจุบันของ symbol จาก Binance Testnet (default = self.symbol)
        คืนค่าเป็น float เช่น 2136.50
        """
        symbol = symbol or self.symbol
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            price = float(ticker["last"])
            logger.debug(f"ราคาปัจจุบัน {symbol}: {price}")
            return price
        except Exception as e:
            logger.error(f"get_current_price error: {e}")
            raise

    async def get_ohlcv(self, timeframe: str = "1h", limit: int = 200, symbol: Optional[str] = None) -> pd.DataFrame:
        """
        ดึงข้อมูล OHLCV สำหรับคำนวณ indicators
        timeframe: '5m', '15m', '1h', '4h', '1d'
        symbol: ระบุเพื่อดึงข้อมูล asset อื่น (default = self.symbol)
        คืน DataFrame มี columns: timestamp, open, high, low, close, volume
        """
        symbol = symbol or self.symbol
        try:
            raw = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp")
            # แปลง dtype เป็น float เพื่อให้ pandas-ta คำนวณได้
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            logger.debug(f"get_ohlcv {symbol} {timeframe}: {len(df)} candles")
            return df
        except Exception as e:
            logger.error(f"get_ohlcv error ({symbol} {timeframe}): {e}")
            raise

    async def get_balance(self) -> dict:
        """
        ดึง USDT balance จาก fapi/v2/balance โดยตรง
        ใช้ ccxt private method แทน fetch_balance() เพราะ fetch_balance
        เรียก sapi endpoint ที่ไม่มี testnet equivalent
        """
        try:
            # fapiPrivateV2GetBalance → GET /fapi/v2/balance
            raw = await self.exchange.fapiPrivateV2GetBalance()
            usdt = next((a for a in raw if a.get("asset") == "USDT"), None)
            if not usdt:
                return {"total": 0.0, "free": 0.0, "used": 0.0}
            total = float(usdt.get("balance", 0) or 0)
            free  = float(usdt.get("availableBalance", 0) or 0)
            used  = round(total - free, 4)
            result = {"total": total, "free": free, "used": used}
            logger.debug(f"Balance: {result}")
            return result
        except Exception as e:
            logger.error(f"get_balance error: {e}")
            return {"total": 0.0, "free": 0.0, "used": 0.0}

    async def get_open_positions(self) -> list:
        """
        ดึง open positions จาก fapi/v2/positionRisk โดยตรง
        กรองเฉพาะ symbol ที่กำหนดและมี size ≠ 0
        """
        try:
            # แปลง "ETH/USDT:USDT" → "ETHUSDT" สำหรับ Binance API
            binance_symbol = self.symbol.replace("/", "").replace(":USDT", "")
            raw = await self.exchange.fapiPrivateV2GetPositionRisk(
                {"symbol": binance_symbol}
            )
            open_positions = [
                p for p in raw if float(p.get("positionAmt", 0) or 0) != 0
            ]
            logger.debug(f"Open positions: {len(open_positions)}")
            return open_positions
        except Exception as e:
            logger.error(f"get_open_positions error: {e}")
            return []

    async def get_order_book(self, limit: int = 20, symbol: Optional[str] = None) -> dict:
        """
        ดึง order book depth สำหรับ Whale Agent
        คืน {'bids': [...], 'asks': [...]}
        symbol: ระบุเพื่อดึงข้อมูล asset อื่น (default = self.symbol)
        """
        symbol = symbol or self.symbol
        try:
            book = await self.exchange.fetch_order_book(symbol, limit=limit)
            return book
        except Exception as e:
            logger.error(f"get_order_book error ({symbol}): {e}")
            return {"bids": [], "asks": []}

    async def get_recent_trades(self, limit: int = 100, symbol: Optional[str] = None) -> list:
        """
        ดึง recent trades สำหรับ Whale Agent วิเคราะห์ large orders
        symbol: ระบุเพื่อดึงข้อมูล asset อื่น (default = self.symbol)
        """
        symbol = symbol or self.symbol
        try:
            trades = await self.exchange.fetch_trades(symbol, limit=limit)
            return trades
        except Exception as e:
            logger.error(f"get_recent_trades error ({symbol}): {e}")
            return []

    async def get_funding_rate(self, symbol: Optional[str] = None) -> float:
        """
        ดึง funding rate ปัจจุบัน (สำหรับ Sentiment Agent)
        symbol: ระบุเพื่อดึงข้อมูล asset อื่น (default = self.symbol)
        """
        symbol = symbol or self.symbol
        try:
            funding = await self.exchange.fetch_funding_rate(symbol)
            rate = float(funding.get("fundingRate", 0) or 0)
            logger.debug(f"Funding rate ({symbol}): {rate}")
            return rate
        except Exception as e:
            logger.error(f"get_funding_rate error ({symbol}): {e}")
            return 0.0

    async def get_open_interest(self, symbol: Optional[str] = None) -> float:
        """
        ดึง open interest รวม (สำหรับ Sentiment Agent)
        symbol: ระบุเพื่อดึงข้อมูล asset อื่น (default = self.symbol)
        """
        symbol = symbol or self.symbol
        try:
            oi = await self.exchange.fetch_open_interest(symbol)
            value = float(oi.get("openInterestAmount") or oi.get("openInterest") or 0)
            logger.debug(f"Open interest ({symbol}): {value}")
            return value
        except Exception as e:
            logger.error(f"get_open_interest error ({symbol}): {e}")
            return 0.0
