"""
executor.py — ส่ง order ไป Binance Futures Testnet
ทำงานผ่าน ccxt | ต้องผ่าน Risk Agent ก่อนเสมอ (บังคับใน main.py)
"""

import os
from datetime import datetime, timezone
from loguru import logger
from typing import Optional, Tuple

import ccxt.async_support as ccxt


class Executor:
    """
    จัดการ order lifecycle บน Binance Futures Testnet:
    open_long, open_short, close_position, calculate_tp_sl
    ทุก method ต้อง log error เสมอ — ห้าม silent fail
    """

    def __init__(self, data_fetcher, db):
        self.data_fetcher = data_fetcher
        self.db = db
        self.exchange = data_fetcher.exchange  # reuse connection จาก DataFetcher
        self.symbol = os.getenv("TRADING_SYMBOL", "ETH/USDT:USDT")
        self._current_trade_id: Optional[int] = None
        # Safety guard: ต้องตั้ง TRADING_ENABLED=true ใน .env จึงจะส่ง order ได้
        self.trading_enabled = os.getenv("TRADING_ENABLED", "false").lower() == "true"
        if not self.trading_enabled:
            logger.warning("Executor: TRADING_ENABLED=false — วิเคราะห์อย่างเดียว ไม่ส่ง order")

    def calculate_tp_sl(
        self, side: str, entry: float, atr: float
    ) -> Tuple[float, float]:
        """
        คำนวณ TP และ SL จาก ATR
        TP = entry ± (ATR × 2)
        SL = entry ∓ (ATR × 1)
        """
        if side == "LONG":
            tp = round(entry + atr * 2, 2)
            sl = round(entry - atr * 1, 2)
        else:  # SHORT
            tp = round(entry - atr * 2, 2)
            sl = round(entry + atr * 1, 2)
        return tp, sl

    async def open_long(
        self,
        size: float,
        leverage: int,
        tp: float,
        sl: float,
        reason: str = "",
        grade: str = "",
    ) -> Optional[dict]:
        """
        เปิด LONG position:
        1. ตั้ง leverage
        2. ส่ง market order BUY
        3. ตั้ง TP/SL orders
        4. บันทึกลง database
        """
        return await self._open_position("LONG", size, leverage, tp, sl, reason, grade)

    async def open_short(
        self,
        size: float,
        leverage: int,
        tp: float,
        sl: float,
        reason: str = "",
        grade: str = "",
    ) -> Optional[dict]:
        """เปิด SHORT position"""
        return await self._open_position("SHORT", size, leverage, tp, sl, reason, grade)

    async def _open_position(
        self,
        side: str,
        size: float,
        leverage: int,
        tp: float,  # noqa
        sl: float,
        reason: str = "",
        grade: str = "",
    ) -> Optional[dict]:
        """
        Internal method สำหรับ open position ทั้ง LONG และ SHORT
        """
        if not self.trading_enabled:
            price = await self.data_fetcher.get_current_price()
            logger.warning(
                f"Executor: TRADING_ENABLED=false — SIMULATED {side} "
                f"size={size} ETH leverage={leverage}x TP={tp} SL={sl}"
            )
            trade_id = await self.db.save_trade(
                side=side,
                entry_price=price,
                size=size,
                tp_price=tp,
                sl_price=sl,
                reason=reason,
                grade=grade,
            )
            self._current_trade_id = trade_id
            return {"simulated": True, "side": side, "size": size, "tp": tp, "sl": sl, "trade_id": trade_id}

        try:
            logger.info(
                f"Executor: Opening {side} | size={size} ETH | "
                f"leverage={leverage}x | TP={tp} | SL={sl}"
            )

            # Step 1 — ตั้ง leverage
            await self.exchange.set_leverage(leverage, self.symbol)
            logger.debug(f"Leverage set: {leverage}x")

            # Step 2 — ส่ง market order
            ccxt_side = "buy" if side == "LONG" else "sell"
            order = await self.exchange.create_market_order(
                symbol=self.symbol,
                side=ccxt_side,
                amount=size,
            )
            entry_price = float(order.get("average") or order.get("price", 0))
            logger.info(f"Market order filled: {order['id']} at {entry_price}")

            # Step 3 — ตั้ง TP order (take profit)
            tp_side = "sell" if side == "LONG" else "buy"
            tp_order = await self.exchange.create_order(
                symbol=self.symbol,
                type="TAKE_PROFIT_MARKET",
                side=tp_side,
                amount=size,
                params={"stopPrice": tp, "closePosition": True},
            )
            logger.debug(f"TP order: {tp_order['id']} at {tp}")

            # Step 4 — ตั้ง SL order (stop loss)
            sl_order = await self.exchange.create_order(
                symbol=self.symbol,
                type="STOP_MARKET",
                side=tp_side,
                amount=size,
                params={"stopPrice": sl, "closePosition": True},
            )
            logger.debug(f"SL order: {sl_order['id']} at {sl}")

            # Step 5 — บันทึกลง database
            trade_id = await self.db.save_trade(
                side=side,
                entry_price=entry_price,
                size=size,
                tp_price=tp,
                sl_price=sl,
                reason=reason,
                grade=grade,
            )
            self._current_trade_id = trade_id

            return {
                "trade_id": trade_id,
                "side": side,
                "entry_price": entry_price,
                "size": size,
                "tp": tp,
                "sl": sl,
                "order_id": order["id"],
            }

        except Exception as e:
            logger.error(f"Executor._open_position ({side}) error: {e}")
            return None

    async def close_position(self) -> Optional[dict]:
        """
        ปิด position ปัจจุบันด้วย market order
        อัปเดตสถานะใน database
        """
        try:
            positions = await self.data_fetcher.get_open_positions()
            if not positions:
                logger.warning("Executor.close_position: ไม่มี position ที่เปิดอยู่")
                return None

            position = positions[0]
            contracts = float(position.get("contracts", 0))
            pos_side = position.get("side", "long")

            # ปิด position ด้วย market order ฝั่งตรงข้าม
            close_side = "sell" if pos_side == "long" else "buy"
            order = await self.exchange.create_market_order(
                symbol=self.symbol,
                side=close_side,
                amount=abs(contracts),
                params={"reduceOnly": True},
            )

            exit_price = float(order.get("average") or order.get("price", 0))
            pnl = float(position.get("unrealizedPnl", 0))

            logger.info(f"Position closed at {exit_price} | PnL: {pnl:.2f}")

            # อัปเดต database
            if self._current_trade_id:
                await self.db.close_trade(
                    trade_id=self._current_trade_id,
                    exit_price=exit_price,
                    pnl=pnl,
                    status="CLOSED",
                )
                self._current_trade_id = None

            return {"exit_price": exit_price, "pnl": pnl}

        except Exception as e:
            logger.error(f"Executor.close_position error: {e}")
            return None
