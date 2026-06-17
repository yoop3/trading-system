"""
wyckoff_btc_agent.py — วิเคราะห์ BTC 1D ด้วย Wyckoff Method
อัปเดตทุก 4 ชั่วโมง | rule-based | ตรวจหา Accumulation/Distribution phases

Wyckoff Events:
  Accumulation: PS → SC → AR → ST → Phase C (Spring) → SOS → LPS → Phase E (Markup)
  Distribution: PSY → BC → AR → ST → SOW → LPSY → Phase E (Markdown)

อ้างอิง: Wyckoff-Method-Wyckoff-Analytics-English-V2.pdf
"""

import numpy as np
from datetime import datetime, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal


class WyckoffBTCAgent(BaseAgent):
    """
    ตรวจหา Wyckoff phases จาก BTC 1D candles + volume
    BTC cycles: ปกติ 3-6 เดือน per phase (สั้นกว่า XAU)
    คะแนน: -3 ถึง +3
    +3 = SOS detected (Accumulation Phase D — markup imminent)
    -3 = SOW detected (Distribution Phase D — markdown imminent)
    """

    BTC_SYMBOL = "BTC/USDT:USDT"
    LOOKBACK = 60  # วัน

    def __init__(self, data_fetcher, db):
        super().__init__("wyckoff_btc", data_fetcher, db)

    async def analyze(self) -> AgentSignal:
        score = 0.0
        reasons = []
        price = 0.0

        try:
            df = await self.data_fetcher.get_ohlcv("1d", limit=self.LOOKBACK, symbol=self.BTC_SYMBOL)
            price = float(df["close"].iloc[-1])

            closes = df["close"].values.astype(float)
            highs = df["high"].values.astype(float)
            lows = df["low"].values.astype(float)
            volumes = df["volume"].values.astype(float)
            opens = df["open"].values.astype(float)

            avg_vol = float(np.mean(volumes[-30:]))
            avg_body = float(np.mean(np.abs(closes[-30:] - opens[-30:])))

            # --- Accumulation signals (bullish) ---

            # Spring: ราคาทะลุต่ำกว่า 20-day low แล้วดีด (Phase C)
            low_20 = float(np.min(lows[-21:-1]))
            spring = (
                lows[-1] < low_20 and
                closes[-1] > low_20 and
                volumes[-1] < avg_vol * 1.5  # volume ไม่สูงมาก
            )
            if spring:
                score += 2
                reasons.append(f"BTC Spring: ทะลุ {low_20:,.0f} แล้วดีดกลับ +2")

            # SOS (Sign of Strength): strong up candle, volume สูง, breaks range
            body_last = abs(closes[-1] - opens[-1])
            sos = (
                closes[-1] > opens[-1] and
                body_last >= avg_body * 2.0 and
                volumes[-1] >= avg_vol * 1.5 and
                closes[-1] > float(np.max(highs[-10:-1]))
            )
            if sos:
                score += 3
                reasons.append("BTC SOS: Bullish displacement บน high volume +3")
            elif not spring:
                # LPS: pullback บน low volume หลัง uptrend
                trend_up = all(closes[-1] > closes[-i-1] for i in range(1, 4))
                lps = trend_up and volumes[-1] < avg_vol * 0.7
                if lps:
                    score += 1
                    reasons.append("BTC LPS: pullback low volume ใน uptrend +1")

            # SC (Selling Climax): volume สูงมาก + reversal
            sc = (
                not spring and not sos and
                volumes[-2] >= avg_vol * 2.5 and
                closes[-2] < opens[-2] and
                abs(closes[-2] - opens[-2]) >= avg_body * 2.0 and
                closes[-1] > closes[-2]
            )
            if sc:
                score += 1
                reasons.append("BTC SC (Selling Climax): reversal หลัง panic volume +1")

            # --- Distribution signals (bearish) ---

            # UTAD: ทะลุ 20-day high แล้วร่วง (Phase C)
            high_20 = float(np.max(highs[-21:-1]))
            utad = (
                highs[-1] > high_20 and
                closes[-1] < high_20 and
                volumes[-1] >= avg_vol * 1.2
            )
            if utad:
                score -= 2
                reasons.append(f"BTC UTAD: ทะลุ {high_20:,.0f} แล้วร่วงกลับ -2")

            # SOW (Sign of Weakness): strong down candle, breaks below range
            sow = (
                closes[-1] < opens[-1] and
                abs(closes[-1] - opens[-1]) >= avg_body * 2.0 and
                volumes[-1] >= avg_vol * 1.5 and
                closes[-1] < float(np.min(lows[-10:-1]))
            )
            if sow:
                score -= 3
                reasons.append("BTC SOW: Bearish displacement บน high volume -3")

            # BC (Buying Climax): volume สูงมาก + up candle at high zone + reversal
            bc = (
                not utad and not sow and
                volumes[-2] >= avg_vol * 2.5 and
                closes[-2] > opens[-2] and
                abs(closes[-2] - opens[-2]) >= avg_body * 2.0 and
                closes[-2] > float(np.percentile(closes, 80)) and
                closes[-1] < closes[-2]
            )
            if bc:
                score -= 1
                reasons.append("BTC BC (Buying Climax): reversal หลัง euphoria volume -1")

            # Volume trend analysis
            if len(volumes) >= 10 and not any([spring, sos, utad, sow]):
                down_vols = [volumes[-i] for i in range(1, 6) if closes[-i] < opens[-i]]
                up_vols = [volumes[-i] for i in range(1, 6) if closes[-i] > opens[-i]]
                if down_vols and up_vols:
                    avg_down_vol = float(np.mean(down_vols))
                    avg_up_vol = float(np.mean(up_vols))
                    if avg_up_vol > avg_down_vol * 1.3:
                        score += 1
                        reasons.append("BTC Volume: สูงบน up days (demand dominant) +1")
                    elif avg_down_vol > avg_up_vol * 1.3:
                        score -= 1
                        reasons.append("BTC Volume: สูงบน down days (supply dominant) -1")

            score = max(-3.0, min(3.0, score))
            confidence = max(0.10, abs(score) / 3.0)  # ขั้นต่ำ 10% เมื่อ agent ทำงานสำเร็จ

            if score >= 1:
                signal = "LONG"
            elif score <= -1:
                signal = "SHORT"
            else:
                signal = "HOLD"

            if not reasons:
                reasons.append("ไม่พบ BTC Wyckoff pattern ชัดเจนใน 60 วัน")

        except Exception as e:
            logger.error(f"WyckoffBTCAgent analyze error: {e}")
            score, confidence, signal = 0.0, 0.0, "HOLD"
            reasons = [f"Error: {e}"]

        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            score=round(score, 2),
            confidence=round(confidence, 3),
            reason=" | ".join(reasons),
            timestamp=datetime.now(timezone.utc).isoformat(),
            next_action="อัปเดตอีกครั้งใน 4 ชั่วโมง",
            price=price,
        )
