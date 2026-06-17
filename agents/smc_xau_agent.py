"""
smc_xau_agent.py — SMC Agent สำหรับ XAU/USDT:USDT (Gold Futures)
Top-down: 1H FVG + 4H structure → London/NY session → 5m stop hunt → displacement → OB

XAU-specific features:
- Round number liquidity: $50 intervals ($3000, $3050, $3100, ...)
- London 07-10 UTC (14-17 UTC+7) = HIGH IMPACT สำหรับ Gold
- News avoidance: configurable via XAU_NEWS_AVOIDANCE=true (.env)

อ้างอิง: SMC PDF + SMC_Agent_Spec.md
"""

import os
from datetime import datetime, timezone
from typing import Optional
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal
from agents.smc_agent.detectors.fvg import detect_htf_fvg
from agents.smc_agent.detectors.liquidity import detect_htf_liquidity
from agents.smc_agent.detectors.session import in_killzone, get_session_name
from agents.smc_agent.detectors.stop_hunt import detect_stop_hunt
from agents.smc_agent.detectors.displacement import detect_displacement
from agents.smc_agent.detectors.order_block import detect_order_block
from agents.smc_agent.detectors.entry import check_entry
from agents.smc_agent.detectors.scoring import calculate_score


XAU_SMC_CONFIG = {
    "symbol":     "XAU/USDT:USDT",
    "asset":      "XAUUSDT",
    "htf":        "1h",
    "ltf":        "5m",
    "htf_lookback":  50,
    "ltf_lookback":  30,
    "min_fvg_size":  2.0,   # Gold gap ≥ $2
    "max_fvg_fill_pct": 75.0,
    "liquidity_lookback": 20,
    "liquidity_eq_threshold_pct": 0.1,
    "stop_hunt_lookback": 10,
    "displacement_max_bars_after": 3,
    "displacement_avg_body_bars": 10,
    "sl_buffer":   15.0,    # Gold SL buffer $15
    "tp1_rr":      1.5,
    "min_tp2_rr":  1.5,
    "min_score_to_signal": 2,
    "sessions": {
        "london":   (7, 10),   # 14:00-17:00 UTC+7 — HIGH IMPACT สำหรับ Gold
        "new_york": (12, 15),  # 19:00-22:00 UTC+7
    },
    # US macro release times (UTC) ที่ควรหลีกเลี่ยง ±30 นาที
    "news_avoid_times_utc": [(8, 30), (13, 30), (14, 0), (18, 0)],
    "news_avoid_window_min": 30,
}

# Round number liquidity สำหรับ Gold (ทุก $50)
_GOLD_ROUND_LEVELS = [x * 50 for x in range(50, 80)]  # $2500..$3950


def _round_number_liquidity(price: float) -> dict:
    """คืน nearest round number level เหนือ/ใต้ราคาปัจจุบัน"""
    above = [l for l in _GOLD_ROUND_LEVELS if l > price]
    below = [l for l in _GOLD_ROUND_LEVELS if l < price]
    return {
        "bsl": float(min(above)) if above else None,
        "ssl": float(max(below)) if below else None,
    }


def _near_major_news(now_utc: datetime, avoid_times: list, window_min: int) -> bool:
    """True ถ้าอยู่ใน window_min นาที ก่อน/หลัง major US economic release"""
    current_minutes = now_utc.hour * 60 + now_utc.minute
    for h, m in avoid_times:
        release_minutes = h * 60 + m
        if abs(current_minutes - release_minutes) <= window_min:
            return True
    return False


class SMCXAUAgent(BaseAgent):
    """
    XAU/USDT SMC analysis:
    1H FVG + session filter + round number liquidity → 5m stop hunt → displacement → OB → entry
    News avoidance: ถ้า XAU_NEWS_AVOIDANCE=true จะ VETO ช่วง major US news
    """

    def __init__(self, data_fetcher, db):
        super().__init__("smc_xau", data_fetcher, db)
        self.config = XAU_SMC_CONFIG
        self.last_smc_output: Optional[dict] = None
        self._news_avoidance = os.getenv("XAU_NEWS_AVOIDANCE", "false").lower() == "true"

    async def analyze(self) -> AgentSignal:
        cfg = self.config
        now = datetime.now(timezone.utc)
        price = 0.0

        try:
            df_htf = await self.data_fetcher.get_ohlcv(cfg["htf"], limit=cfg["htf_lookback"], symbol=cfg["symbol"])
            df_5m  = await self.data_fetcher.get_ohlcv(cfg["ltf"], limit=cfg["ltf_lookback"], symbol=cfg["symbol"])
            price = float(df_5m.iloc[-1]["close"])

            # STEP 1 — HTF FVG (1H)
            fvgs = detect_htf_fvg(df_htf, min_gap=cfg["min_fvg_size"], max_fill_pct=cfg["max_fvg_fill_pct"])
            active_fvgs = [f for f in fvgs if f["active"]]
            logger.debug(f"[smc_xau] FVGs 1H: {len(fvgs)} total, {len(active_fvgs)} active")

            # STEP 2 — Liquidity: รวม swing levels + round numbers
            liquidity = detect_htf_liquidity(
                df_htf, price,
                lookback=cfg["liquidity_lookback"],
                eq_threshold_pct=cfg["liquidity_eq_threshold_pct"],
            )
            round_liq = _round_number_liquidity(price)
            # ถ้า round number ใกล้กว่า swing level ให้ใช้ round number แทน
            if round_liq["bsl"] and (liquidity["bsl"] is None or round_liq["bsl"] < liquidity["bsl"]):
                liquidity["bsl"] = round_liq["bsl"]
            if round_liq["ssl"] and (liquidity["ssl"] is None or round_liq["ssl"] > liquidity["ssl"]):
                liquidity["ssl"] = round_liq["ssl"]

            # STEP 3 — Session Filter
            in_session = in_killzone(now, cfg["sessions"])
            session_name = get_session_name(now, cfg["sessions"])

            # STEP 4 — News Avoidance (XAU_NEWS_AVOIDANCE=true → skip ช่วง major news)
            near_news = False
            if self._news_avoidance:
                near_news = _near_major_news(
                    now, cfg["news_avoid_times_utc"], cfg["news_avoid_window_min"]
                )
                if near_news:
                    logger.info(f"[smc_xau] Near major US news ({now.strftime('%H:%M')} UTC) — SKIP")

            # STEP 5 — Stop Hunt (5m)
            stop_hunt = detect_stop_hunt(df_5m, active_fvgs, lookback=cfg["stop_hunt_lookback"])

            # STEP 6 — Displacement (5m)
            displacement = detect_displacement(
                df_5m, stop_hunt,
                max_bars_after=cfg["displacement_max_bars_after"],
                avg_body_bars=cfg["displacement_avg_body_bars"],
            )

            # STEP 7 — Order Block (5m)
            ob = detect_order_block(df_5m, displacement)

            # STEP 8 — Entry Signal
            entry_result = check_entry(df_5m, ob, active_fvgs)
            entry_signal = entry_result["signal"]

            levels, rr_ok = self._calculate_levels(entry_signal, ob, liquidity, price, cfg)

            # News avoidance เป็น criteria เพิ่มเติม — ถ้า near_news → ทำเป็น session=False ให้ลด score
            in_session_eff = in_session and not near_news

            criteria = {
                "htf_fvg": len(active_fvgs) > 0,
                "session": in_session_eff,
                "stop_hunt": stop_hunt["detected"],
                "displacement": displacement["detected"],
                "ob_detected": ob is not None,
                "rr_ok": rr_ok,
            }

            score_result = calculate_score(criteria, entry_signal)
            final_signal   = score_result["signal"]
            score          = score_result["score"]
            confidence_pct = score_result["confidence"]

            reason = self._build_reason(
                active_fvgs, session_name, stop_hunt, displacement, ob, near_news, round_liq, price
            )

            relevant_fvg = self._find_relevant_fvg(active_fvgs, entry_signal)

            self.last_smc_output = {
                "agent": "SMC_XAU",
                "timestamp": now.isoformat(),
                "asset": cfg["asset"],
                "signal": final_signal,
                "score": score,
                "confidence": confidence_pct,
                "criteria": {
                    "htf_fvg_active": len(active_fvgs) > 0,
                    "in_session": in_session,
                    "near_news": near_news,
                    "stop_hunt": stop_hunt["detected"],
                    "displacement": displacement["detected"],
                    "ob_detected": ob is not None,
                    "rr_sufficient": rr_ok,
                },
                "levels": levels,
                "min_score_to_signal": cfg["min_score_to_signal"],
                "min_tp2_rr": cfg["min_tp2_rr"],
                "context": {
                    "fvg_fill_pct": relevant_fvg["fill_pct"] if relevant_fvg else None,
                    "session": session_name,
                    "htf_trend": "BULLISH" if relevant_fvg and relevant_fvg["type"] == "BULL" else "BEARISH" if relevant_fvg else "NEUTRAL",
                    "liquidity_above": liquidity["bsl"],
                    "liquidity_below": liquidity["ssl"],
                    "round_level_above": round_liq["bsl"],
                    "round_level_below": round_liq["ssl"],
                },
                "reason": reason,
            }

            agent_signal_type = final_signal if final_signal in ("LONG", "SHORT") else "HOLD"

        except Exception as e:
            logger.error(f"SMCXAUAgent analyze error: {e}")
            self.last_smc_output = None
            agent_signal_type, score, confidence_pct, reason = "HOLD", 0, 0.0, f"Error: {e}"

        return AgentSignal(
            agent_name=self.name,
            signal=agent_signal_type,
            score=float(score),
            confidence=round(confidence_pct / 100, 3),
            reason=reason,
            timestamp=now.isoformat(),
            next_action="วิเคราะห์ XAU SMC รอบถัดไปใน 5 นาที",
            price=price,
        )

    def _calculate_levels(self, entry_signal, ob, liquidity, price, cfg):
        if entry_signal not in ("LONG", "SHORT") or ob is None:
            return None, False
        sl_buffer = cfg["sl_buffer"]
        if entry_signal == "LONG":
            sl = ob["bottom"] - sl_buffer
            risk = price - sl
            tp2 = liquidity.get("bsl")
        else:
            sl = ob["top"] + sl_buffer
            risk = sl - price
            tp2 = liquidity.get("ssl")
        if risk <= 0:
            return None, False
        tp1 = price + risk * cfg["tp1_rr"] if entry_signal == "LONG" else price - risk * cfg["tp1_rr"]
        rr_tp2 = round(abs(tp2 - price) / risk, 2) if tp2 is not None else 0.0
        rr_ok = rr_tp2 >= cfg["min_tp2_rr"]
        return {
            "entry": round(price, 2),
            "sl":    round(sl, 2),
            "tp1":   round(tp1, 2),
            "tp2":   round(tp2, 2) if tp2 is not None else None,
            "rr_tp1": round(abs(tp1 - price) / risk, 2),
            "rr_tp2": rr_tp2,
        }, rr_ok

    def _find_relevant_fvg(self, active_fvgs, entry_signal):
        if not active_fvgs:
            return None
        if entry_signal not in ("LONG", "SHORT"):
            return active_fvgs[-1]
        fvg_type = "BULL" if entry_signal == "LONG" else "BEAR"
        matching = [f for f in active_fvgs if f["type"] == fvg_type]
        return matching[-1] if matching else active_fvgs[-1]

    def _build_reason(self, active_fvgs, session_name, stop_hunt, displacement, ob, near_news, round_liq, price):
        parts = []
        if active_fvgs:
            fvg = active_fvgs[-1]
            trend = "Bullish" if fvg["type"] == "BULL" else "Bearish"
            parts.append(f"{trend} FVG 1H active ({fvg['fill_pct']:.0f}% fill)")
        else:
            parts.append("ไม่มี HTF FVG 1H active")
        if near_news:
            parts.append("ใกล้ major US news — session VETO")
        elif stop_hunt["detected"]:
            parts.append(f"Stop Hunt ใน {session_name}")
        else:
            parts.append("ไม่พบ Stop Hunt")
        disp = "Displacement"
        if displacement["detected"] and displacement.get("msb"):
            disp += " + MSB"
        parts.append(disp if displacement["detected"] else "ไม่พบ Displacement")
        parts.append("OB confirmed" if ob is not None else "ไม่พบ OB")
        # Round number info
        if round_liq["bsl"] and round_liq["ssl"]:
            parts.append(f"Round liq: BSL={round_liq['bsl']:,.0f} / SSL={round_liq['ssl']:,.0f}")
        return " | ".join(parts)
