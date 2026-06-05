"""
sentiment_agent.py — วิเคราะห์ market sentiment จาก Fear & Greed, Funding Rate, Open Interest
อัปเดตทุก 15 นาที | rule-based | contrarian approach สำหรับ F&G
"""

import httpx
from datetime import datetime, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal


class SentimentAgent(BaseAgent):
    """
    วิเคราะห์ sentiment ด้วย 3 แหล่งข้อมูล:
    1. Fear & Greed Index (alternative.me) — contrarian
    2. Funding Rate จาก Binance — longs/shorts pressure
    3. Open Interest — trend confirmation
    """

    def __init__(self, data_fetcher, db):
        super().__init__("sentiment", data_fetcher, db)
        # เก็บ OI รอบก่อนเพื่อเปรียบเทียบ trend
        self._prev_open_interest: float = 0.0

    async def _get_fear_greed(self) -> int:
        """
        ดึง Fear & Greed Index จาก alternative.me
        คืน 0-100 (0=Extreme Fear, 100=Extreme Greed)
        ถ้า error คืน 50 (neutral)
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get("https://api.alternative.me/fng/")
                data = resp.json()
                return int(data["data"][0]["value"])
        except Exception as e:
            logger.warning(f"Fear & Greed API error: {e}, ใช้ค่า neutral 50")
            return 50

    async def analyze(self) -> AgentSignal:
        score = 0.0
        reasons = []
        next_action = ""

        try:
            # ดึงข้อมูลพร้อมกัน
            fg_index = await self._get_fear_greed()
            funding_rate = await self.data_fetcher.get_funding_rate()
            open_interest = await self.data_fetcher.get_open_interest()
            price = await self.data_fetcher.get_current_price()

            # Rule 1 — Fear & Greed Index (contrarian)
            if fg_index <= 25:
                score += 3
                reasons.append(f"F&G Extreme Fear ({fg_index}) → contrarian LONG +3")
                next_action = "F&G สูงขึ้น = confidence ลด รอ rebound"
            elif fg_index <= 45:
                score += 1
                reasons.append(f"F&G Fear ({fg_index}) +1")
                next_action = "รอ F&G ลงต่ำกว่า 25 เพื่อ signal ชัด"
            elif fg_index <= 55:
                reasons.append(f"F&G Neutral ({fg_index})")
                next_action = "F&G neutral รอทิศทาง"
            elif fg_index <= 75:
                score -= 1
                reasons.append(f"F&G Greed ({fg_index}) -1")
                next_action = "รอ F&G สูงกว่า 75 เพื่อ SHORT signal"
            else:
                score -= 3
                reasons.append(f"F&G Extreme Greed ({fg_index}) → contrarian SHORT -3")
                next_action = "F&G Extreme Greed รอ sell-off"

            # Rule 2 — Funding Rate
            if funding_rate > 0.001:  # > 0.1%
                score -= 2
                reasons.append(f"Funding rate สูง ({funding_rate:.4%}) longs overbought -2")
            elif funding_rate < -0.001:  # < -0.1%
                score += 2
                reasons.append(f"Funding rate ลบ ({funding_rate:.4%}) shorts overbought +2")
            else:
                reasons.append(f"Funding rate neutral ({funding_rate:.4%})")

            # Rule 3 — Open Interest + Price direction
            if self._prev_open_interest > 0 and open_interest > 0:
                oi_change = (open_interest - self._prev_open_interest) / self._prev_open_interest
                if oi_change > 0.02:  # OI เพิ่มขึ้น > 2%
                    # ต้องรู้ทิศทางราคาด้วย
                    # ใช้ score ปัจจุบันเป็น proxy ของทิศทางราคา
                    if score >= 0:
                        score += 1
                        reasons.append(f"OI เพิ่ม trend confirm +1")
                    else:
                        score -= 1
                        reasons.append(f"OI เพิ่ม + ราคาลง short squeeze warning -1")
            self._prev_open_interest = open_interest

            score = max(-6.0, min(6.0, score))
            confidence = abs(score) / 6.0

            if score >= 2:
                signal = "LONG"
            elif score <= -2:
                signal = "SHORT"
            else:
                signal = "HOLD"

        except Exception as e:
            logger.error(f"SentimentAgent analyze error: {e}")
            score, confidence, signal = 0.0, 0.0, "HOLD"
            reasons = [f"Error: {e}"]
            price = 0.0
            next_action = "เกิด error รอ retry"

        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            score=round(score, 2),
            confidence=round(confidence, 3),
            reason=" | ".join(reasons) if reasons else "ไม่มีสัญญาณ",
            timestamp=datetime.now(timezone.utc).isoformat(),
            next_action=next_action or "อัปเดตอีกครั้งใน 15 นาที",
            price=price,
        )
