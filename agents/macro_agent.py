"""
macro_agent.py — วิเคราะห์ macro trend จากกราฟ BTC 4H และ 1D
อัปเดตทุก 4 ชั่วโมง | rule-based | ดู market structure, EMA200, weekly, monthly trend
"""

from datetime import datetime, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal
from core.indicators import Indicators


class MacroAgent(BaseAgent):
    """
    วิเคราะห์ macro trend ด้วย 3 rules:
    1. Market Structure (HH/HL vs LH/LL) บน 1D
    2. EMA 200 บน 4H
    3. Weekly trend (7 วัน)
    """

    def __init__(self, data_fetcher, db):
        super().__init__("macro", data_fetcher, db)
        self.indicators = Indicators()

    BTC_SYMBOL = "BTC/USDT:USDT"

    async def analyze(self) -> AgentSignal:
        score = 0.0
        reasons = []
        next_action = ""

        try:
            # ใช้ BTC/USDT:USDT สำหรับ macro analysis (ไม่ขึ้นกับ self.symbol)
            df_4h = await self.data_fetcher.get_ohlcv("4h", limit=200, symbol=self.BTC_SYMBOL)
            df_1d = await self.data_fetcher.get_ohlcv("1d", limit=60, symbol=self.BTC_SYMBOL)

            df_4h = self.indicators.calculate_all(df_4h)
            ind_4h = self.indicators.get_latest(df_4h)

            price = float(ind_4h.get("close", 0))

            # Rule 1 — Market Structure บน 1D: HH/HL หรือ LH/LL
            if len(df_1d) >= 10:
                recent = df_1d.tail(10)
                highs = recent["high"].values
                lows = recent["low"].values
                hh = highs[-1] > highs[-3] and highs[-3] > highs[-5]
                hl = lows[-1] > lows[-3] and lows[-3] > lows[-5]
                lh = highs[-1] < highs[-3] and highs[-3] < highs[-5]
                ll = lows[-1] < lows[-3] and lows[-3] < lows[-5]

                if hh and hl:
                    score += 3
                    reasons.append("BTC Market Structure: HH/HL uptrend +3")
                    next_action = "BTC Uptrend — รอ pullback เพื่อ LONG"
                elif lh and ll:
                    score -= 3
                    reasons.append("BTC Market Structure: LH/LL downtrend -3")
                    next_action = "BTC Downtrend — รอ bounce เพื่อ SHORT"
                else:
                    reasons.append("BTC Market Structure: ranging")
                    next_action = "BTC Ranging — รอ breakout"

            # Rule 2 — EMA 200 บน 4H
            ema200 = ind_4h.get("EMA_200")
            if ema200 and price:
                if price > ema200:
                    score += 2
                    reasons.append(f"BTC เหนือ EMA200 4H ({ema200:,.0f}) +2")
                else:
                    score -= 2
                    reasons.append(f"BTC ใต้ EMA200 4H ({ema200:,.0f}) -2")

            # Rule 3 — Weekly trend: เทียบราคากับ 7 วันที่แล้ว
            if len(df_1d) >= 8:
                price_7d_ago = float(df_1d["close"].iloc[-8])
                if price_7d_ago > 0:
                    weekly_change = (price - price_7d_ago) / price_7d_ago
                    if weekly_change >= 0.05:
                        score += 2
                        reasons.append(f"Weekly +{weekly_change:.1%} bullish +2")
                    elif weekly_change <= -0.05:
                        score -= 2
                        reasons.append(f"Weekly {weekly_change:.1%} bearish -2")
                    else:
                        reasons.append(f"Weekly {weekly_change:+.1%} neutral")

            # Rule 4 — Monthly trend: เทียบราคากับ 30 วันที่แล้ว
            if len(df_1d) >= 32:
                price_30d_ago = float(df_1d["close"].iloc[-32])
                if price_30d_ago > 0:
                    monthly_change = (price - price_30d_ago) / price_30d_ago
                    if monthly_change >= 0.15:
                        score += 2
                        reasons.append(f"Monthly +{monthly_change:.1%} strong bull +2")
                    elif monthly_change <= -0.15:
                        score -= 2
                        reasons.append(f"Monthly {monthly_change:.1%} strong bear -2")
                    else:
                        reasons.append(f"Monthly {monthly_change:+.1%} neutral")

            score = max(-9.0, min(9.0, score))
            confidence = min(abs(score) / 9.0, 1.0)

            if score >= 2:
                signal = "LONG"
            elif score <= -2:
                signal = "SHORT"
            else:
                signal = "HOLD"

        except Exception as e:
            logger.error(f"MacroAgent analyze error: {e}")
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
            next_action=next_action or "อัปเดตอีกครั้งใน 4 ชั่วโมง",
            price=price,
        )
