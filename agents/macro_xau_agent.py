"""
macro_xau_agent.py — วิเคราะห์ XAU macro trend จากกราฟ 4H และ 1D
อัปเดตทุก 4 ชั่วโมง | rule-based
Rules: Market Structure, EMA200 4H, Weekly trend, Economic Calendar (CPI/NFP/Fed)
"""

import aiohttp
from datetime import datetime, timedelta, timezone
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal
from core.indicators import Indicators

# High impact USD events ที่กระทบ XAU มากที่สุด
_HIGH_IMPACT_KEYWORDS = {"cpi", "nfp", "non-farm", "nonfarm", "fomc", "fed", "interest rate"}
_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def _parse_event_time(date_str: str, time_str: str, now_utc: datetime) -> datetime | None:
    """
    แปลง "Jun 11, 2026" + "8:30am" → datetime UTC
    Forex Factory calendar ใช้ US Eastern time ไม่มี DST info → ใช้ UTC approximation (ET+5)
    """
    try:
        # แปลง "8:30am" → timedelta
        time_str = time_str.lower().strip()
        if not time_str or time_str in ("all day", "tentative", ""):
            return None
        ampm = "am" if "am" in time_str else "pm"
        time_clean = time_str.replace("am", "").replace("pm", "").strip()
        parts = time_clean.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

        # แปลง "Jun 11, 2026"
        dt_naive = datetime.strptime(date_str.strip(), "%b %d, %Y")
        # ET → UTC: ET+5 (approximate, ignoring DST for simplicity)
        dt_utc = dt_naive.replace(hour=hour, minute=minute, tzinfo=timezone.utc) + timedelta(hours=5)
        return dt_utc
    except Exception:
        return None


async def _check_high_impact_news(now_utc: datetime, window_hours: int = 24) -> tuple[bool, str]:
    """
    ดึง Forex Factory calendar → หา USD High Impact events ใน window_hours ข้างหน้า
    คืน (has_event, event_title)
    """
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.get(_CALENDAR_URL) as resp:
                if resp.status != 200:
                    return False, ""
                data = await resp.json(content_type=None)

        deadline = now_utc + timedelta(hours=window_hours)
        for event in data:
            if event.get("country", "").upper() != "USD":
                continue
            if event.get("impact", "").lower() != "high":
                continue
            title = event.get("title", "").lower()
            if not any(kw in title for kw in _HIGH_IMPACT_KEYWORDS):
                continue
            dt = _parse_event_time(
                event.get("date", ""),
                event.get("time", ""),
                now_utc,
            )
            if dt and now_utc <= dt <= deadline:
                return True, event.get("title", "")
    except Exception as e:
        logger.warning(f"[macro_xau] economic calendar fetch error: {e}")
    return False, ""


class MacroXAUAgent(BaseAgent):
    """
    วิเคราะห์ XAU macro trend ด้วย 4 rules + economic calendar guard:
    1. Market Structure (HH/HL vs LH/LL) บน 1D
    2. EMA 200 บน 4H: above=+2, below=-2
    3. Weekly trend: ±5%/week = ±2
    4. Monthly trend: ±10%/month = ±2 (XAU ช้ากว่า BTC)
    Economic Calendar: USD High Impact event ใน 24h → confidence ×0.7
    """

    XAU_SYMBOL = "XAU/USDT:USDT"

    def __init__(self, data_fetcher, db):
        super().__init__("macro_xau", data_fetcher, db)
        self.indicators = Indicators()

    async def analyze(self) -> AgentSignal:
        score = 0.0
        reasons = []
        next_action = ""
        price = 0.0
        now_utc = datetime.now(timezone.utc)

        try:
            df_4h = await self.data_fetcher.get_ohlcv("4h", limit=200, symbol=self.XAU_SYMBOL)
            df_1d = await self.data_fetcher.get_ohlcv("1d", limit=60, symbol=self.XAU_SYMBOL)

            df_4h = self.indicators.calculate_all(df_4h)
            ind_4h = self.indicators.get_latest(df_4h)

            price = float(ind_4h.get("close", 0))

            # Rule 1 — Market Structure บน 1D
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
                    reasons.append("XAU Market Structure: HH/HL uptrend +3")
                    next_action = "XAU Uptrend — รอ pullback เพื่อ LONG"
                elif lh and ll:
                    score -= 3
                    reasons.append("XAU Market Structure: LH/LL downtrend -3")
                    next_action = "XAU Downtrend — รอ bounce เพื่อ SHORT"
                else:
                    reasons.append("XAU Market Structure: ranging")
                    next_action = "XAU Ranging — รอ breakout"

            # Rule 2 — EMA 200 บน 4H (XAU เหนือ EMA200 = safe haven demand)
            ema200 = ind_4h.get("EMA_200")
            if ema200 and price:
                if price > ema200:
                    score += 2
                    reasons.append(f"XAU เหนือ EMA200 4H ({ema200:,.1f}) +2")
                else:
                    score -= 2
                    reasons.append(f"XAU ใต้ EMA200 4H ({ema200:,.1f}) -2")

            # Rule 3 — Weekly trend
            if len(df_1d) >= 8:
                price_7d_ago = float(df_1d["close"].iloc[-8])
                if price_7d_ago > 0:
                    weekly_change = (price - price_7d_ago) / price_7d_ago
                    if weekly_change >= 0.05:
                        score += 2
                        reasons.append(f"XAU Weekly +{weekly_change:.1%} bullish +2")
                    elif weekly_change <= -0.05:
                        score -= 2
                        reasons.append(f"XAU Weekly {weekly_change:.1%} bearish -2")
                    else:
                        reasons.append(f"XAU Weekly {weekly_change:+.1%} neutral")

            # Rule 4 — Monthly trend (threshold 10% เพราะ XAU เคลื่อนที่ช้ากว่า BTC)
            if len(df_1d) >= 32:
                price_30d_ago = float(df_1d["close"].iloc[-32])
                if price_30d_ago > 0:
                    monthly_change = (price - price_30d_ago) / price_30d_ago
                    if monthly_change >= 0.10:
                        score += 2
                        reasons.append(f"XAU Monthly +{monthly_change:.1%} strong bull +2")
                    elif monthly_change <= -0.10:
                        score -= 2
                        reasons.append(f"XAU Monthly {monthly_change:.1%} strong bear -2")
                    else:
                        reasons.append(f"XAU Monthly {monthly_change:+.1%} neutral")

            score = max(-9.0, min(9.0, score))
            confidence = min(abs(score) / 9.0, 1.0)
            confidence = max(0.10, confidence)  # ขั้นต่ำ 10% เมื่อ agent ทำงานสำเร็จ

            # Economic Calendar: USD High Impact event ใน 24h → confidence ×0.7
            has_news, news_title = await _check_high_impact_news(now_utc, window_hours=24)
            if has_news:
                confidence = confidence * 0.7
                reasons.append(f"⚠️ USD High Impact event ใน 24h: '{news_title}' → conf -30%")
                next_action = f"รอ {news_title} ผ่านไปก่อน"

            if score >= 2:
                signal = "LONG"
            elif score <= -2:
                signal = "SHORT"
            else:
                signal = "HOLD"

        except Exception as e:
            logger.error(f"MacroXAUAgent analyze error: {e}")
            score, confidence, signal = 0.0, 0.0, "HOLD"
            reasons = [f"Error: {e}"]
            next_action = "เกิด error รอ retry"

        return AgentSignal(
            agent_name=self.name,
            signal=signal,
            score=round(score, 2),
            confidence=round(confidence, 3),
            reason=" | ".join(reasons) if reasons else "ไม่มีสัญญาณ XAU macro",
            timestamp=now_utc.isoformat(),
            next_action=next_action or "อัปเดตอีกครั้งใน 4 ชั่วโมง",
            price=price,
        )
