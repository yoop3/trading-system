"""
whale_btc_agent.py — ตรวจจับ BTC whale activity จาก order book และ large trades
อัปเดตทุก 15 นาที | rule-based | order book 20 levels, bid/ask ratio, large trades >$500k
"""

from datetime import datetime, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal


class WhaleBTCAgent(BaseAgent):
    """
    วิเคราะห์ BTC whale activity ด้วย 2 signals:
    1. Order Book imbalance (bid vs ask volume) — 20 levels
    2. Recent large BTC trades > $500,000 USD
    คะแนน: -4 ถึง +4
    """

    BTC_SYMBOL = "BTC/USDT:USDT"
    LARGE_TRADE_THRESHOLD_USD = 500_000  # $500K = whale trade สำหรับ BTC

    def __init__(self, data_fetcher, db):
        super().__init__("whale_btc", data_fetcher, db)

    async def analyze(self) -> AgentSignal:
        score = 0.0
        reasons = []
        next_action = ""
        price = 0.0

        try:
            price = await self.data_fetcher.get_current_price(symbol=self.BTC_SYMBOL)
            order_book = await self.data_fetcher.get_order_book(limit=20, symbol=self.BTC_SYMBOL)
            recent_trades = await self.data_fetcher.get_recent_trades(limit=100, symbol=self.BTC_SYMBOL)

            # Rule 1 — Order Book Imbalance (20 levels bid vs ask)
            bids = order_book.get("bids", [])
            asks = order_book.get("asks", [])

            if bids and asks:
                # volume = price × size (USD value ที่แต่ละ level)
                bid_volume = sum(float(b[0]) * float(b[1]) for b in bids if len(b) >= 2)
                ask_volume = sum(float(a[0]) * float(a[1]) for a in asks if len(a) >= 2)

                if ask_volume > 0:
                    ratio = bid_volume / ask_volume
                    if ratio > 1.5:
                        score += 2
                        reasons.append(f"BTC Bid/Ask ratio {ratio:.2f} buying pressure +2")
                        next_action = "BTC whale กำลังซื้อ รอ momentum"
                    elif ratio < 0.67:
                        score -= 2
                        reasons.append(f"BTC Bid/Ask ratio {ratio:.2f} selling pressure -2")
                        next_action = "BTC whale กำลังขาย รอ momentum"
                    else:
                        reasons.append(f"BTC Bid/Ask ratio {ratio:.2f} balanced")
                        next_action = "BTC order book สมดุล รอ imbalance"

            # Rule 2 — Large BTC Trades (> $500K)
            if recent_trades and price > 0:
                whale_buys = 0.0
                whale_sells = 0.0

                for trade in recent_trades:
                    amount = float(trade.get("amount", 0) or 0)
                    trade_price = float(trade.get("price", price) or price)
                    usd_value = amount * trade_price
                    side = trade.get("side", "")

                    if usd_value >= self.LARGE_TRADE_THRESHOLD_USD:
                        if side == "buy":
                            whale_buys += usd_value
                        elif side == "sell":
                            whale_sells += usd_value

                total_whale = whale_buys + whale_sells
                if total_whale > 0:
                    if whale_buys > whale_sells * 1.5:
                        score += 2
                        reasons.append(f"BTC whale buys ${whale_buys/1e6:.1f}M >> sells +2")
                    elif whale_sells > whale_buys * 1.5:
                        score -= 2
                        reasons.append(f"BTC whale sells ${whale_sells/1e6:.1f}M >> buys -2")
                    else:
                        reasons.append(
                            f"BTC whale mixed (buy ${whale_buys/1e6:.1f}M / sell ${whale_sells/1e6:.1f}M)"
                        )
                else:
                    reasons.append(f"ไม่พบ BTC whale trades > ${self.LARGE_TRADE_THRESHOLD_USD/1e6:.1f}M")
                    next_action = f"รอ large BTC order > ${self.LARGE_TRADE_THRESHOLD_USD/1e3:.0f}K"

            score = max(-4.0, min(4.0, score))
            confidence = abs(score) / 4.0 if score != 0 else 0.0

            if score >= 2:
                signal = "LONG"
            elif score <= -2:
                signal = "SHORT"
            else:
                signal = "HOLD"

        except Exception as e:
            logger.error(f"WhaleBTCAgent analyze error: {e}")
            score, confidence, signal = 0.0, 0.0, "HOLD"
            reasons = [f"Error: {e}"]
            next_action = "เกิด error รอ retry"

        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            score=round(score, 2),
            confidence=round(confidence, 3),
            reason=" | ".join(reasons) if reasons else "ไม่มีสัญญาณ BTC whale",
            timestamp=datetime.now(timezone.utc).isoformat(),
            next_action=next_action or "อัปเดต BTC whale อีกครั้งใน 15 นาที",
            price=price,
        )
