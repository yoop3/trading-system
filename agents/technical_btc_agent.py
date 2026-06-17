"""
technical_btc_agent.py — Technical analysis สำหรับ BTC/USDT:USDT
อัปเดตทุก 5 นาที | rule-based | 1H/15m/5m timeframes
EMA20/50, RSI14, MACD, Bollinger Bands, Volume ratio
"""

from datetime import datetime, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal
from core.indicators import Indicators


class TechnicalBTCAgent(BaseAgent):
    """
    วิเคราะห์ BTC chart ด้วย rules บน 3 timeframes:
    1H สำหรับ trend + RSI + BB, 15m สำหรับ MACD, 5m สำหรับ EMA + Volume
    คะแนน: -9 ถึง +9 | confidence = |score|/9
    """

    BTC_SYMBOL = "BTC/USDT:USDT"

    def __init__(self, data_fetcher, db):
        super().__init__("technical_btc", data_fetcher, db)
        self.indicators = Indicators()

    async def analyze(self) -> AgentSignal:
        score = 0.0
        reasons = []
        next_action = ""
        price = 0.0

        try:
            df_5m  = await self.data_fetcher.get_ohlcv("5m",  limit=100, symbol=self.BTC_SYMBOL)
            df_15m = await self.data_fetcher.get_ohlcv("15m", limit=100, symbol=self.BTC_SYMBOL)
            df_1h  = await self.data_fetcher.get_ohlcv("1h",  limit=200, symbol=self.BTC_SYMBOL)

            df_5m  = self.indicators.calculate_all(df_5m)
            df_15m = self.indicators.calculate_all(df_15m)
            df_1h  = self.indicators.calculate_all(df_1h)

            ind_5m  = self.indicators.get_latest(df_5m)
            ind_15m = self.indicators.get_latest(df_15m)
            ind_1h  = self.indicators.get_latest(df_1h)

            price = float(ind_5m.get("close", 0))

            # Rule 1 — EMA Trend (5m): price > EMA20 > EMA50 = bullish alignment
            ema20 = ind_5m.get("EMA_20")
            ema50 = ind_5m.get("EMA_50")
            if ema20 and ema50 and price:
                if price > ema20 > ema50:
                    score += 2
                    reasons.append("EMA 5m bullish stack +2")
                    next_action = "BTC EMA stack ยืนยัน uptrend"
                elif price < ema20 < ema50:
                    score -= 2
                    reasons.append("EMA 5m bearish stack -2")
                    next_action = "BTC EMA stack ยืนยัน downtrend"

            # Rule 2 — RSI 14 (1H): oversold / overbought
            rsi = ind_1h.get("RSI_14")
            if rsi:
                if rsi < 30:
                    score += 3
                    reasons.append(f"RSI 1H oversold ({rsi:.1f}) +3")
                    next_action = f"BTC RSI={rsi:.1f} oversold รอ bounce"
                elif rsi > 70:
                    score -= 3
                    reasons.append(f"RSI 1H overbought ({rsi:.1f}) -3")
                    next_action = f"BTC RSI={rsi:.1f} overbought รอ correction"
                elif 40 <= rsi <= 60:
                    reasons.append(f"RSI 1H neutral ({rsi:.1f})")

            # Rule 3 — MACD (15m): momentum direction
            macd     = ind_15m.get("MACD")
            macd_sig = ind_15m.get("MACD_signal")
            macd_hist = ind_15m.get("MACD_hist")
            hist_prev = float(df_15m["MACD_hist"].iloc[-2]) if len(df_15m) > 1 else 0
            if macd is not None and macd_sig is not None and macd_hist is not None:
                if macd > macd_sig and macd_hist > hist_prev:
                    score += 2
                    reasons.append("MACD 15m bullish momentum +2")
                elif macd < macd_sig and macd_hist < hist_prev:
                    score -= 2
                    reasons.append("MACD 15m bearish momentum -2")

            # Rule 4 — Volume surge (5m)
            vol_ratio = ind_5m.get("Volume_ratio")
            if vol_ratio and vol_ratio > 2.0:
                score += 1 if score > 0 else -1
                reasons.append(f"Volume surge 5m ({vol_ratio:.1f}x) ±1")

            # Rule 5 — Bollinger Bands (1H): price near bands
            bb_upper = ind_1h.get("BB_upper")
            bb_lower = ind_1h.get("BB_lower")
            if bb_upper and bb_lower and price:
                bb_range = bb_upper - bb_lower
                if bb_range > 0:
                    if price <= bb_lower + 0.05 * bb_range:
                        score += 1
                        reasons.append("ราคาแตะ BB lower 1H +1")
                    elif price >= bb_upper - 0.05 * bb_range:
                        score -= 1
                        reasons.append("ราคาแตะ BB upper 1H -1")

            score = max(-9.0, min(9.0, score))
            confidence = abs(score) / 9.0

            if score >= 2:
                signal = "LONG"
            elif score <= -2:
                signal = "SHORT"
            else:
                signal = "HOLD"

        except Exception as e:
            logger.error(f"TechnicalBTCAgent analyze error: {e}")
            score, confidence, signal = 0.0, 0.0, "HOLD"
            reasons = [f"Error: {e}"]
            next_action = "เกิด error รอ retry"

        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            score=round(score, 2),
            confidence=round(confidence, 3),
            reason=" | ".join(reasons) if reasons else "ไม่มีสัญญาณ BTC technical ชัดเจน",
            timestamp=datetime.now(timezone.utc).isoformat(),
            next_action=next_action or "วิเคราะห์ BTC กราฟรอบถัดไปใน 5 นาที",
            price=price,
        )
