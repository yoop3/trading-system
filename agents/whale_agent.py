"""
whale_agent.py — ตรวจจับ whale activity จาก order book และ large trades
อัปเดตทุก 15 นาที | rule-based | ดู bid/ask imbalance และ large orders
"""

from datetime import datetime, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal


class WhaleAgent(BaseAgent):
    """
    วิเคราะห์ whale activity ด้วย 2 signals:
    1. Order Book imbalance (bid vs ask volume)
    2. Recent large trades (> $100,000)
    """

    LARGE_TRADE_THRESHOLD_USD = 100_000  # trade ที่ใหญ่กว่านี้คือ whale trade

    def __init__(self, data_fetcher, db):
        super().__init__("whale", data_fetcher, db)

    async def analyze(self) -> AgentSignal:
        score = 0.0
        reasons = []
        next_action = ""
        price = 0.0

        try:
            price = await self.data_fetcher.get_current_price()
            order_book = await self.data_fetcher.get_order_book(limit=20)
            recent_trades = await self.data_fetcher.get_recent_trades(limit=100)

            # Rule 1 — Order Book Imbalance
            bids = order_book.get("bids", [])
            asks = order_book.get("asks", [])

            if bids and asks:
                # รวม volume ทุก level (price * size)
                bid_volume = sum(float(b[1]) for b in bids if len(b) >= 2)
                ask_volume = sum(float(a[1]) for a in asks if len(a) >= 2)

                if ask_volume > 0:
                    ratio = bid_volume / ask_volume
                    if ratio > 1.5:
                        score += 2
                        reasons.append(f"Bid/Ask ratio {ratio:.2f} buying pressure +2")
                        next_action = "Whale กำลังซื้อ รอ momentum ยืนยัน"
                    elif ratio < 0.67:  # 1/1.5 = 0.67
                        score -= 2
                        reasons.append(f"Bid/Ask ratio {ratio:.2f} selling pressure -2")
                        next_action = "Whale กำลังขาย รอ momentum ยืนยัน"
                    else:
                        reasons.append(f"Bid/Ask ratio {ratio:.2f} balanced")
                        next_action = "Order book สมดุล รอ imbalance"

            # Rule 2 — Large Trades Analysis
            if recent_trades and price > 0:
                whale_buys = 0.0  # USD value ของ whale buys
                whale_sells = 0.0  # USD value ของ whale sells

                for trade in recent_trades:
                    # ccxt trade format: amount = size in ETH
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
                        reasons.append(f"Whale buys ${whale_buys:,.0f} >> sells +2")
                    elif whale_sells > whale_buys * 1.5:
                        score -= 2
                        reasons.append(f"Whale sells ${whale_sells:,.0f} >> buys -2")
                    else:
                        reasons.append(
                            f"Whale activity mixed (buy ${whale_buys:,.0f} / sell ${whale_sells:,.0f})"
                        )
                else:
                    reasons.append("ไม่พบ whale trades ในรอบนี้")
                    next_action = "รอ large order > $100K"

            score = max(-4.0, min(4.0, score))
            confidence = abs(score) / 4.0 if score != 0 else 0.0

            if score >= 2:
                signal = "LONG"
            elif score <= -2:
                signal = "SHORT"
            else:
                signal = "HOLD"

        except Exception as e:
            logger.error(f"WhaleAgent analyze error: {e}")
            score, confidence, signal = 0.0, 0.0, "HOLD"
            reasons = [f"Error: {e}"]
            next_action = "เกิด error รอ retry"

        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            score=round(score, 2),
            confidence=round(confidence, 3),
            reason=" | ".join(reasons) if reasons else "ไม่มีสัญญาณ whale",
            timestamp=datetime.now(timezone.utc).isoformat(),
            next_action=next_action or "อัปเดตอีกครั้งใน 15 นาที",
            price=price,
        )
