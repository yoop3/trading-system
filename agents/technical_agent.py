"""
technical_agent.py — วิเคราะห์ technical analysis ด้วย rule-based logic
อัปเดตทุก 5 นาที | ใช้กราฟ 5m, 15m, 1H | ไม่ใช้ LLM (ประหยัด token)
"""

from datetime import datetime, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal
from core.indicators import Indicators


class TechnicalAgent(BaseAgent):
    """
    วิเคราะห์ chart ด้วย rules:
    EMA trend, RSI overbought/oversold, MACD momentum, Volume surge, Bollinger Bands
    คะแนนรวม: -9 ถึง +9 → confidence = |score| / 9
    """

    def __init__(self, data_fetcher, db):
        super().__init__("technical", data_fetcher, db)
        self.indicators = Indicators()

    async def analyze(self) -> AgentSignal:
        """
        ดึงกราฟ 3 timeframes แล้วคำนวณ score จาก 5 rules
        """
        score = 0.0
        reasons = []
        next_action = ""

        try:
            # ดึงข้อมูล 3 timeframes พร้อมกัน
            df_5m = await self.data_fetcher.get_ohlcv("5m", limit=100)
            df_1h = await self.data_fetcher.get_ohlcv("1h", limit=200)

            df_5m = self.indicators.calculate_all(df_5m)
            df_1h = self.indicators.calculate_all(df_1h)

            ind_5m = self.indicators.get_latest(df_5m)
            ind_1h = self.indicators.get_latest(df_1h)

            price = float(ind_5m.get("close", 0))

            # Rule 1 — EMA Trend (5m): ราคา vs EMA20 vs EMA50
            ema20 = ind_5m.get("EMA_20")
            ema50 = ind_5m.get("EMA_50")
            if ema20 and ema50 and price:
                if price > ema20 > ema50:
                    score += 2
                    reasons.append("EMA bullish +2")
                    next_action = "รอ EMA alignment ยืนยัน"
                elif price < ema20 < ema50:
                    score -= 2
                    reasons.append("EMA bearish -2")
                    next_action = "รอราคาทะลุ EMA20 ขึ้นไป"
                else:
                    next_action = "EMA ยังไม่เรียง รอสัญญาณ"

            # Rule 2 — RSI (1H): oversold/overbought
            rsi = ind_1h.get("RSI_14")
            if rsi:
                if rsi < 30:
                    score += 3
                    reasons.append(f"RSI oversold ({rsi:.1f}) +3")
                    next_action = f"RSI={rsi:.1f} oversold รอ bounce"
                elif rsi > 70:
                    score -= 3
                    reasons.append(f"RSI overbought ({rsi:.1f}) -3")
                    next_action = f"RSI={rsi:.1f} overbought รอ correction"
                elif 40 <= rsi <= 60:
                    reasons.append(f"RSI neutral ({rsi:.1f})")

            # Rule 3 — MACD (5m): momentum direction
            macd = ind_5m.get("MACD")
            macd_sig = ind_5m.get("MACD_signal")
            macd_hist = ind_5m.get("MACD_hist")
            # เปรียบเทียบ histogram ปัจจุบันกับแถวก่อนหน้า
            hist_prev = float(df_5m["MACD_hist"].iloc[-2]) if len(df_5m) > 1 else 0
            if macd is not None and macd_sig is not None and macd_hist is not None:
                if macd > macd_sig and macd_hist > hist_prev:
                    score += 2
                    reasons.append("MACD bullish +2")
                elif macd < macd_sig and macd_hist < hist_prev:
                    score -= 2
                    reasons.append("MACD bearish -2")

            # Rule 4 — Volume surge: volume > 2x average
            vol_ratio = ind_5m.get("Volume_ratio")
            if vol_ratio and vol_ratio > 2.0:
                score += 1 if score > 0 else -1
                reasons.append(f"Volume surge ({vol_ratio:.1f}x) ±1")

            # Rule 5 — Bollinger Bands (1H): price near bands
            bb_upper = ind_1h.get("BB_upper")
            bb_lower = ind_1h.get("BB_lower")
            if bb_upper and bb_lower and price:
                bb_range = bb_upper - bb_lower
                if bb_range > 0:
                    # ถ้าราคาอยู่ใน 5% ของ lower band
                    if price <= bb_lower + 0.05 * bb_range:
                        score += 1
                        reasons.append("ราคาแตะ BB lower +1")
                    # ถ้าราคาอยู่ใน 5% ของ upper band
                    elif price >= bb_upper - 0.05 * bb_range:
                        score -= 1
                        reasons.append("ราคาแตะ BB upper -1")

            # จำกัด score ไม่ให้เกิน -9 ถึง +9
            score = max(-9.0, min(9.0, score))
            confidence = abs(score) / 9.0

            if score > 0:
                signal = "LONG"
            elif score < 0:
                signal = "SHORT"
            else:
                signal = "HOLD"

        except Exception as e:
            logger.error(f"TechnicalAgent analyze error: {e}")
            score, confidence, signal = 0.0, 0.0, "HOLD"
            reasons = [f"Error: {e}"]
            price = 0.0
            next_action = "เกิด error รอ retry"

        reason_str = " | ".join(reasons) if reasons else "ไม่มีสัญญาณชัดเจน"

        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            score=round(score, 2),
            confidence=round(confidence, 3),
            reason=reason_str,
            timestamp=datetime.now(timezone.utc).isoformat(),
            next_action=next_action or "วิเคราะห์กราฟรอบถัดไปใน 5 นาที",
            price=price,
        )
