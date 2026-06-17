"""
smc_btc_agent.py — SMC Agent สำหรับ BTC/USDT:USDT (Top-down analysis)
HTF (4H/1D): ดู FVG + market structure (CHoCH/BOS)
MTF (1H): ดู session filter + structure confirmation
LTF (5m): ดู stop hunt + displacement + OB สำหรับ entry

อ้างอิง: SMC PDF + SMC_Agent_Spec.md — ใช้ detectors เดิมจาก smc_agent/
"""

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


BTC_SMC_CONFIG = {
    "symbol":     "BTC/USDT:USDT",
    "asset":      "BTCUSDT",
    "htf":        "4h",       # HTF FVG จาก 4H (ใหญ่กว่า XAU ที่ใช้ 1H)
    "ltf":        "5m",
    "htf_lookback":  100,     # 100 × 4H = 400 ชั่วโมง ≈ 16 วัน
    "ltf_lookback":  30,
    "min_fvg_size":  100.0,   # BTC gap ≥ $100 (scale ราคา BTC >> XAU)
    "max_fvg_fill_pct": 75.0,
    "liquidity_lookback": 20,
    "liquidity_eq_threshold_pct": 0.1,
    "stop_hunt_lookback": 10,
    "displacement_max_bars_after": 3,
    "displacement_avg_body_bars": 10,
    "sl_buffer":   500.0,     # BTC SL buffer $500 (เทียบ XAU $15)
    "tp1_rr":      1.5,
    "min_tp2_rr":  1.5,
    "min_score_to_signal": 2,
    "sessions": {
        "london":   (7, 10),  # 07:00-10:00 UTC = 14:00-17:00 UTC+7
        "new_york": (12, 15), # 12:00-15:00 UTC = 19:00-22:00 UTC+7
    },
}


def _detect_htf_structure(df) -> dict:
    """
    ตรวจ BTC market structure จาก higher timeframe candles
    คืน: {"trend": "BULLISH"|"BEARISH"|"RANGING", "bos_bull": bool, "bos_bear": bool}
    ใช้สำหรับ top-down: ถ้า HTF bearish → ให้น้ำหนัก SHORT setup เท่านั้น
    """
    if len(df) < 10:
        return {"trend": "RANGING", "bos_bull": False, "bos_bear": False}

    highs  = df["high"].values[-10:].astype(float)
    lows   = df["low"].values[-10:].astype(float)
    closes = df["close"].values[-10:].astype(float)

    # HH/HL = bullish, LH/LL = bearish
    hh = closes[-1] > highs[-4] if len(highs) >= 4 else False
    hl = lows[-1] > lows[-4]   if len(lows) >= 4 else False
    lh = closes[-1] < highs[-4] if len(highs) >= 4 else False
    ll = lows[-1] < lows[-4]   if len(lows) >= 4 else False

    if hh and hl:
        trend = "BULLISH"
    elif lh and ll:
        trend = "BEARISH"
    else:
        trend = "RANGING"

    # BOS: ราคา close ล่าสุดทะลุ swing high/low ก่อนหน้า
    swing_high = float(max(highs[:-2])) if len(highs) > 2 else highs[0]
    swing_low  = float(min(lows[:-2]))  if len(lows) > 2  else lows[0]
    bos_bull = closes[-1] > swing_high
    bos_bear = closes[-1] < swing_low

    return {"trend": trend, "bos_bull": bos_bull, "bos_bear": bos_bear}


class SMCBTCAgent(BaseAgent):
    """
    Top-down BTC SMC analysis:
    4H FVG + 1D structure → session → 5m stop hunt → displacement → OB → entry
    Scoring: 6 criteria → -3..+3 (ใช้ scoring.py เดิม)
    """

    def __init__(self, data_fetcher, db):
        super().__init__("smc_btc", data_fetcher, db)
        self.config = BTC_SMC_CONFIG
        self.last_smc_output: Optional[dict] = None

    async def analyze(self) -> AgentSignal:
        cfg = self.config
        now = datetime.now(timezone.utc)
        price = 0.0

        try:
            df_htf = await self.data_fetcher.get_ohlcv(cfg["htf"], limit=cfg["htf_lookback"], symbol=cfg["symbol"])
            df_1d  = await self.data_fetcher.get_ohlcv("1d", limit=30, symbol=cfg["symbol"])
            df_5m  = await self.data_fetcher.get_ohlcv(cfg["ltf"], limit=cfg["ltf_lookback"], symbol=cfg["symbol"])
            price = float(df_5m.iloc[-1]["close"])

            # STEP 1 — HTF FVG (4H)
            fvgs = detect_htf_fvg(df_htf, min_gap=cfg["min_fvg_size"], max_fill_pct=cfg["max_fvg_fill_pct"])
            active_fvgs = [f for f in fvgs if f["active"]]
            logger.debug(f"[smc_btc] FVGs 4H: {len(fvgs)} total, {len(active_fvgs)} active")

            # STEP 2 — HTF Liquidity (4H)
            liquidity = detect_htf_liquidity(
                df_htf, price,
                lookback=cfg["liquidity_lookback"],
                eq_threshold_pct=cfg["liquidity_eq_threshold_pct"],
            )

            # STEP 3 — 1D Market Structure (CHoCH/BOS)
            structure = _detect_htf_structure(df_1d)
            htf_trend = structure["trend"]
            logger.debug(f"[smc_btc] 1D structure: {htf_trend}")

            # STEP 4 — Session Filter
            in_session = in_killzone(now, cfg["sessions"])
            session_name = get_session_name(now, cfg["sessions"])

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

            # HTF trend alignment — แทน "htf_fvg" criterion
            htf_fvg_ok = len(active_fvgs) > 0
            htf_trend_ok = (
                (entry_signal == "LONG"  and htf_trend in ("BULLISH", "RANGING")) or
                (entry_signal == "SHORT" and htf_trend in ("BEARISH", "RANGING")) or
                entry_signal not in ("LONG", "SHORT")
            )

            levels, rr_ok = self._calculate_levels(entry_signal, ob, liquidity, price, cfg)

            criteria = {
                "htf_fvg": htf_fvg_ok and htf_trend_ok,  # FVG + HTF trend aligned
                "session": in_session,
                "stop_hunt": stop_hunt["detected"],
                "displacement": displacement["detected"],
                "ob_detected": ob is not None,
                "rr_ok": rr_ok,
            }

            score_result = calculate_score(criteria, entry_signal)
            final_signal  = score_result["signal"]
            score         = score_result["score"]
            confidence_pct = score_result["confidence"]

            reason = self._build_reason(active_fvgs, htf_trend, session_name, stop_hunt, displacement, ob)

            self.last_smc_output = {
                "agent": "SMC_BTC",
                "timestamp": now.isoformat(),
                "asset": cfg["asset"],
                "signal": final_signal,
                "score": score,
                "confidence": confidence_pct,
                "criteria": {
                    "htf_fvg_active": htf_fvg_ok,
                    "htf_trend": htf_trend,
                    "in_session": in_session,
                    "stop_hunt": stop_hunt["detected"],
                    "displacement": displacement["detected"],
                    "ob_detected": ob is not None,
                    "rr_sufficient": rr_ok,
                },
                "levels": levels,
                "min_score_to_signal": cfg["min_score_to_signal"],
                "min_tp2_rr": cfg["min_tp2_rr"],
                "context": {
                    "session": session_name,
                    "htf_trend": htf_trend,
                    "liquidity_above": liquidity["bsl"],
                    "liquidity_below": liquidity["ssl"],
                },
                "reason": reason,
            }

            agent_signal_type = final_signal if final_signal in ("LONG", "SHORT") else "HOLD"

        except Exception as e:
            logger.error(f"SMCBTCAgent analyze error: {e}")
            self.last_smc_output = None
            agent_signal_type, score, confidence_pct, reason = "HOLD", 0, 0.0, f"Error: {e}"

        return AgentSignal(
            agent_name=self.name,
            signal=agent_signal_type,
            score=float(score),
            confidence=round(confidence_pct / 100, 3),
            reason=reason,
            timestamp=now.isoformat(),
            next_action="วิเคราะห์ BTC SMC รอบถัดไปใน 5 นาที",
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
        rr_tp1 = round(abs(tp1 - price) / risk, 2)
        rr_tp2 = round(abs(tp2 - price) / risk, 2) if tp2 is not None else 0.0
        rr_ok = rr_tp2 >= cfg["min_tp2_rr"]
        return {
            "entry": round(price, 0),
            "sl":    round(sl, 0),
            "tp1":   round(tp1, 0),
            "tp2":   round(tp2, 0) if tp2 is not None else None,
            "rr_tp1": rr_tp1,
            "rr_tp2": rr_tp2,
        }, rr_ok

    def _build_reason(self, active_fvgs, htf_trend, session_name, stop_hunt, displacement, ob):
        parts = [f"HTF trend: {htf_trend}"]
        parts.append(f"{len(active_fvgs)} active FVG (4H)" if active_fvgs else "ไม่มี FVG 4H")
        parts.append(f"Stop Hunt ใน {session_name}" if stop_hunt["detected"] else "ไม่พบ Stop Hunt")
        disp = "Displacement"
        if displacement["detected"] and displacement.get("msb"):
            disp += " + MSB"
        parts.append(disp if displacement["detected"] else "ไม่พบ Displacement")
        parts.append("OB confirmed" if ob is not None else "ไม่พบ OB")
        return " | ".join(parts)
