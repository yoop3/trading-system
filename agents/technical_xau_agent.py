"""
technical_xau_agent.py — Technical analysis สำหรับ XAU/USDT:USDT (Gold Futures)
อัปเดตทุก 5 นาที | rule-based | 1H/15m/5m timeframes
เพิ่ม ATR volatility check: ถ้า ATR > 2x average → confidence ลดเหลือ 50% (Gold volatile ขณะ news)
"""

from datetime import datetime, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal
from core.indicators import Indicators


class TechnicalXAUAgent(BaseAgent):
    """
    วิเคราะห์ XAU chart ด้วย rules เดียวกับ BTC แต่:
    - ใช้ XAU/USDT:USDT symbol
    - ATR check: ถ้า volatility สูงเกิน 2x average → confidence ลด 50%
    คะแนน: -9 ถึง +9 | confidence = |score|/9 (ปรับลดถ้า high volatility)
    """

    XAU_SYMBOL = "XAU/USDT:USDT"

    def __init__(self, data_fetcher, db):
        super().__init__("technical_xau", data_fetcher, db)
        self.indicators = Indicators()

    async def analyze(self) -> AgentSignal:
        score = 0.0
        reasons = []
        next_action = ""
        price = 0.0
        high_volatility = False

        try:
            df_5m  = await self.data_fetcher.get_ohlcv("5m",  limit=100, symbol=self.XAU_SYMBOL)
            df_15m = await self.data_fetcher.get_ohlcv("15m", limit=100, symbol=self.XAU_SYMBOL)
            df_1h  = await self.data_fetcher.get_ohlcv("1h",  limit=200, symbol=self.XAU_SYMBOL)

            df_5m  = self.indicators.calculate_all(df_5m)
            df_15m = self.indicators.calculate_all(df_15m)
            df_1h  = self.indicators.calculate_all(df_1h)

            ind_5m  = self.indicators.get_latest(df_5m)
            ind_15m = self.indicators.get_latest(df_15m)
            ind_1h  = self.indicators.get_latest(df_1h)

            price = float(ind_5m.get("close", 0))

            # ATR Volatility Check (1H): ถ้า ATR > 2x ค่าเฉลี่ย 20 bar → high volatility
            atr_1h = ind_1h.get("ATR_14")
            if atr_1h and len(df_1h) >= 20:
                avg_atr = float(df_1h["ATR_14"].dropna().tail(20).mean())
                if avg_atr > 0 and float(atr_1h) > avg_atr * 2.0:
                    high_volatility = True
                    reasons.append(f"⚠️ XAU ATR สูงผิดปกติ ({atr_1h:.1f} vs avg {avg_atr:.1f}) — probably news")

            # Rule 1 — EMA Trend (5m)
            ema20 = ind_5m.get("EMA_20")
            ema50 = ind_5m.get("EMA_50")
            if ema20 and ema50 and price:
                if price > ema20 > ema50:
                    score += 2
                    reasons.append("XAU EMA 5m bullish stack +2")
                elif price < ema20 < ema50:
                    score -= 2
                    reasons.append("XAU EMA 5m bearish stack -2")

            # Rule 2 — RSI 14 (1H)
            rsi = ind_1h.get("RSI_14")
            if rsi:
                if rsi < 30:
                    score += 3
                    reasons.append(f"XAU RSI 1H oversold ({rsi:.1f}) +3")
                    next_action = f"XAU RSI={rsi:.1f} oversold รอ bounce"
                elif rsi > 70:
                    score -= 3
                    reasons.append(f"XAU RSI 1H overbought ({rsi:.1f}) -3")
                    next_action = f"XAU RSI={rsi:.1f} overbought รอ correction"
                elif 40 <= rsi <= 60:
                    reasons.append(f"XAU RSI 1H neutral ({rsi:.1f})")

            # Rule 3 — MACD (15m)
            macd     = ind_15m.get("MACD")
            macd_sig = ind_15m.get("MACD_signal")
            macd_hist = ind_15m.get("MACD_hist")
            hist_prev = float(df_15m["MACD_hist"].iloc[-2]) if len(df_15m) > 1 else 0
            if macd is not None and macd_sig is not None and macd_hist is not None:
                if macd > macd_sig and macd_hist > hist_prev:
                    score += 2
                    reasons.append("XAU MACD 15m bullish +2")
                elif macd < macd_sig and macd_hist < hist_prev:
                    score -= 2
                    reasons.append("XAU MACD 15m bearish -2")

            # Rule 4 — Volume surge (5m)
            vol_ratio = ind_5m.get("Volume_ratio")
            if vol_ratio and vol_ratio > 2.0:
                score += 1 if score > 0 else -1
                reasons.append(f"XAU Volume surge 5m ({vol_ratio:.1f}x) ±1")

            # Rule 5 — Bollinger Bands (1H)
            bb_upper = ind_1h.get("BB_upper")
            bb_lower = ind_1h.get("BB_lower")
            if bb_upper and bb_lower and price:
                bb_range = bb_upper - bb_lower
                if bb_range > 0:
                    if price <= bb_lower + 0.05 * bb_range:
                        score += 1
                        reasons.append("XAU ราคาแตะ BB lower 1H +1")
                    elif price >= bb_upper - 0.05 * bb_range:
                        score -= 1
                        reasons.append("XAU ราคาแตะ BB upper 1H -1")

            score = max(-9.0, min(9.0, score))
            confidence = abs(score) / 9.0

            # ATR reduction: ถ้า high volatility → confidence ลดเหลือ 50% ของค่าเดิม
            if high_volatility:
                confidence = confidence * 0.5
                reasons.append("Confidence ลด 50% เพราะ high volatility")

            if score >= 2:
                signal = "LONG"
            elif score <= -2:
                signal = "SHORT"
            else:
                signal = "HOLD"

        except Exception as e:
            logger.error(f"TechnicalXAUAgent analyze error: {e}")
            score, confidence, signal = 0.0, 0.0, "HOLD"
            reasons = [f"Error: {e}"]
            next_action = "เกิด error รอ retry"

        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            score=round(score, 2),
            confidence=round(confidence, 3),
            reason=" | ".join(reasons) if reasons else "ไม่มีสัญญาณ XAU technical ชัดเจน",
            timestamp=datetime.now(timezone.utc).isoformat(),
            next_action=next_action or "วิเคราะห์ XAU กราฟรอบถัดไปใน 5 นาที",
            price=price,
        )
