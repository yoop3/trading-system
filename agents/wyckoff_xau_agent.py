"""
wyckoff_xau_agent.py — วิเคราะห์ XAU 1D ด้วย Wyckoff Method
อัปเดตทุก 4 ชั่วโมง | rule-based

หมายเหตุ: XAU Wyckoff cycles ยาวกว่า BTC 2-3 เท่า
  BTC: 3-6 เดือน/phase | XAU: 6-18 เดือน/phase
  ดังนั้น lookback 120 วัน (4 เดือน) ให้ภาพที่ดีกว่า 60 วัน

Events:
  Accumulation: PS → SC → AR → ST → Spring (Phase C) → SOS → LPS → Markup (Phase E)
  Distribution: PSY → BC → AR → ST → UTAD (Phase C) → SOW → LPSY → Markdown (Phase E)
"""

import numpy as np
from datetime import datetime, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal


class WyckoffXAUAgent(BaseAgent):
    """
    ตรวจหา Wyckoff phases จาก XAU 1D candles + volume
    XAU cycles ช้ากว่า BTC จึงใช้ LOOKBACK 120 วัน
    คะแนน: -3 ถึง +3
    """

    XAU_SYMBOL = "XAU/USDT:USDT"
    LOOKBACK = 120  # XAU cycles ยาวกว่า BTC จึงดู 4 เดือน

    def __init__(self, data_fetcher, db):
        super().__init__("wyckoff_xau", data_fetcher, db)

    async def analyze(self) -> AgentSignal:
        score = 0.0
        reasons = []
        price = 0.0

        try:
            df = await self.data_fetcher.get_ohlcv("1d", limit=self.LOOKBACK, symbol=self.XAU_SYMBOL)
            price = float(df["close"].iloc[-1])

            closes = df["close"].values.astype(float)
            highs = df["high"].values.astype(float)
            lows = df["low"].values.astype(float)
            volumes = df["volume"].values.astype(float)
            opens = df["open"].values.astype(float)

            # ใช้ 60 วันล่าสุดสำหรับ average (ครึ่งหนึ่งของ lookback)
            avg_vol = float(np.mean(volumes[-60:]))
            avg_body = float(np.mean(np.abs(closes[-60:] - opens[-60:])))

            # --- Accumulation signals (bullish) ---

            # Spring (Phase C): ทะลุ 30-day low แล้วดีด — XAU ใช้ 30 วันแทน 20
            low_30 = float(np.min(lows[-31:-1]))
            spring = (
                lows[-1] < low_30 and
                closes[-1] > low_30 and
                volumes[-1] < avg_vol * 1.5
            )
            if spring:
                score += 2
                reasons.append(f"XAU Spring: ทะลุ {low_30:,.1f} แล้วดีดกลับ +2")

            # SOS (Sign of Strength): bullish displacement, high volume, breaks range
            body_last = abs(closes[-1] - opens[-1])
            sos = (
                closes[-1] > opens[-1] and
                body_last >= avg_body * 2.0 and
                volumes[-1] >= avg_vol * 1.5 and
                closes[-1] > float(np.max(highs[-15:-1]))  # XAU ใช้ 15 แทน 10
            )
            if sos:
                score += 3
                reasons.append("XAU SOS: Bullish displacement บน high volume +3")
            elif not spring:
                # LPS: pullback low volume in uptrend
                trend_up = all(closes[-1] > closes[-i-1] for i in range(1, 4))
                lps = trend_up and volumes[-1] < avg_vol * 0.7
                if lps:
                    score += 1
                    reasons.append("XAU LPS: pullback low volume ใน uptrend +1")

            # SC (Selling Climax): panic volume + reversal
            sc = (
                not spring and not sos and
                volumes[-2] >= avg_vol * 2.5 and
                closes[-2] < opens[-2] and
                abs(closes[-2] - opens[-2]) >= avg_body * 2.0 and
                closes[-1] > closes[-2]
            )
            if sc:
                score += 1
                reasons.append("XAU SC (Selling Climax): reversal หลัง panic volume +1")

            # --- Distribution signals (bearish) ---

            # UTAD (Phase C): ทะลุ 30-day high แล้วร่วง
            high_30 = float(np.max(highs[-31:-1]))
            utad = (
                highs[-1] > high_30 and
                closes[-1] < high_30 and
                volumes[-1] >= avg_vol * 1.2
            )
            if utad:
                score -= 2
                reasons.append(f"XAU UTAD: ทะลุ {high_30:,.1f} แล้วร่วงกลับ -2")

            # SOW (Sign of Weakness): bearish displacement, breaks below range
            sow = (
                closes[-1] < opens[-1] and
                abs(closes[-1] - opens[-1]) >= avg_body * 2.0 and
                volumes[-1] >= avg_vol * 1.5 and
                closes[-1] < float(np.min(lows[-15:-1]))
            )
            if sow:
                score -= 3
                reasons.append("XAU SOW: Bearish displacement บน high volume -3")

            # BC (Buying Climax): euphoria volume at high zone + reversal
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
                reasons.append("XAU BC (Buying Climax): reversal หลัง euphoria volume -1")

            # Volume trend analysis
            if len(volumes) >= 10 and not any([spring, sos, utad, sow]):
                down_vols = [volumes[-i] for i in range(1, 6) if closes[-i] < opens[-i]]
                up_vols = [volumes[-i] for i in range(1, 6) if closes[-i] > opens[-i]]
                if down_vols and up_vols:
                    avg_down_vol = float(np.mean(down_vols))
                    avg_up_vol = float(np.mean(up_vols))
                    if avg_up_vol > avg_down_vol * 1.3:
                        score += 1
                        reasons.append("XAU Volume: สูงบน up days (demand dominant) +1")
                    elif avg_down_vol > avg_up_vol * 1.3:
                        score -= 1
                        reasons.append("XAU Volume: สูงบน down days (supply dominant) -1")

            score = max(-3.0, min(3.0, score))
            confidence = abs(score) / 3.0

            if score >= 1:
                signal = "LONG"
            elif score <= -1:
                signal = "SHORT"
            else:
                signal = "HOLD"

            if not reasons:
                reasons.append("ไม่พบ XAU Wyckoff pattern ชัดเจนใน 120 วัน (XAU cycles ช้า)")

        except Exception as e:
            logger.error(f"WyckoffXAUAgent analyze error: {e}")
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
