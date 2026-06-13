"""
smc_agent.py — SMC Agent: Smart Money Concepts analysis (XAUUSDT)
รัน pipeline ตาม SMC_Agent_Spec.md:
  HTF FVG -> HTF Liquidity -> Session Filter -> Stop Hunt -> Displacement -> Order Block -> Entry -> Scoring
Stateless: คำนวณใหม่ทุก run จาก HTF (1H) + LTF (5m) candles ล่าสุด
ผลลัพธ์เต็ม (criteria/levels/context) เก็บไว้ที่ self.last_smc_output ให้ Risk Agent ใช้ต่อ
"""

from datetime import datetime, timezone
from typing import Optional
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal
from agents.smc_agent.config import SMC_CONFIG
from agents.smc_agent.detectors.fvg import detect_htf_fvg
from agents.smc_agent.detectors.liquidity import detect_htf_liquidity
from agents.smc_agent.detectors.session import in_killzone, get_session_name
from agents.smc_agent.detectors.stop_hunt import detect_stop_hunt
from agents.smc_agent.detectors.displacement import detect_displacement
from agents.smc_agent.detectors.order_block import detect_order_block
from agents.smc_agent.detectors.entry import check_entry
from agents.smc_agent.detectors.scoring import calculate_score


class SMCAgent(BaseAgent):
    """
    วิเคราะห์ XAUUSDT ด้วย Smart Money Concepts (FVG, Liquidity, Stop Hunt, Displacement, Order Block)
    คะแนนรวม: -3 ถึง +3 (criteria_met < 4/6 -> NO_SETUP/HOLD)
    """

    def __init__(self, data_fetcher, db, config: Optional[dict] = None):
        super().__init__("smc", data_fetcher, db)
        self.config = config or SMC_CONFIG
        self.last_smc_output: Optional[dict] = None

    async def analyze(self) -> AgentSignal:
        cfg = self.config
        now = datetime.now(timezone.utc)
        price = 0.0

        try:
            df_1h = await self.data_fetcher.get_ohlcv(cfg["htf"], limit=cfg["htf_lookback"], symbol=cfg["symbol"])
            df_5m = await self.data_fetcher.get_ohlcv(cfg["ltf"], limit=cfg["ltf_lookback"], symbol=cfg["symbol"])
            price = float(df_5m.iloc[-1]["close"])

            # STEP 1 — HTF FVG
            fvgs = detect_htf_fvg(df_1h, min_gap=cfg["min_fvg_size"], max_fill_pct=cfg["max_fvg_fill_pct"])
            active_fvgs = [f for f in fvgs if f["active"]]
            logger.debug(f"[smc] FVGs: {len(fvgs)} total, {len(active_fvgs)} active")

            # STEP 2 — HTF Liquidity
            liquidity = detect_htf_liquidity(
                df_1h, price,
                lookback=cfg["liquidity_lookback"],
                eq_threshold_pct=cfg["liquidity_eq_threshold_pct"],
            )
            logger.debug(f"[smc] Liquidity: {liquidity}")

            # STEP 3 — Session Filter
            in_session = in_killzone(now, cfg["sessions"])
            session_name = get_session_name(now, cfg["sessions"])
            logger.debug(f"[smc] Session: {session_name} (in_killzone={in_session})")

            # STEP 4 — Stop Hunt (5m)
            stop_hunt = detect_stop_hunt(df_5m, active_fvgs, lookback=cfg["stop_hunt_lookback"])
            logger.debug(f"[smc] Stop hunt: {stop_hunt}")

            # STEP 5 — Displacement (5m)
            displacement = detect_displacement(
                df_5m, stop_hunt,
                max_bars_after=cfg["displacement_max_bars_after"],
                avg_body_bars=cfg["displacement_avg_body_bars"],
            )
            logger.debug(f"[smc] Displacement: {displacement}")

            # STEP 6 — Order Block (5m)
            ob = detect_order_block(df_5m, displacement)
            logger.debug(f"[smc] Order block: {ob}")

            # STEP 7 — Entry Signal
            entry_result = check_entry(df_5m, ob, active_fvgs)
            entry_signal = entry_result["signal"]
            logger.debug(f"[smc] Entry signal: {entry_signal}")

            # Levels (entry/sl/tp1/tp2/rr) จาก OB + liquidity targets
            levels, rr_ok = self._calculate_levels(entry_signal, ob, liquidity, price, cfg)

            # FVG ที่เกี่ยวข้องกับ setup นี้ (สำหรับ context.fvg_fill_pct / htf_trend)
            relevant_fvg = self._find_relevant_fvg(active_fvgs, entry_signal)

            criteria = {
                "htf_fvg_active": len(active_fvgs) > 0,
                "in_session": in_session,
                "stop_hunt": stop_hunt["detected"],
                "displacement": displacement["detected"],
                "ob_detected": ob is not None,
                "rr_sufficient": rr_ok,
            }
            score_result = calculate_score(
                {
                    "htf_fvg": criteria["htf_fvg_active"],
                    "session": criteria["in_session"],
                    "stop_hunt": criteria["stop_hunt"],
                    "displacement": criteria["displacement"],
                    "ob_detected": criteria["ob_detected"],
                    "rr_ok": criteria["rr_sufficient"],
                },
                entry_signal,
            )
            final_signal = score_result["signal"]
            score = score_result["score"]
            confidence_pct = score_result["confidence"]

            reason = self._build_reason(relevant_fvg, session_name, stop_hunt, displacement, ob)

            self.last_smc_output = {
                "agent": "SMC",
                "timestamp": now.isoformat(),
                "asset": cfg["asset"],
                "signal": final_signal,
                "score": score,
                "confidence": confidence_pct,
                "criteria": criteria,
                "levels": levels,
                "context": {
                    "fvg_fill_pct": relevant_fvg["fill_pct"] if relevant_fvg else None,
                    "session": session_name,
                    "htf_trend": self._htf_trend(relevant_fvg),
                    "liquidity_above": liquidity["bsl"],
                    "liquidity_below": liquidity["ssl"],
                },
                "reason": reason,
            }

            agent_signal_type = final_signal if final_signal in ("LONG", "SHORT") else "HOLD"

        except Exception as e:
            logger.error(f"SMCAgent analyze error: {e}")
            self.last_smc_output = None
            agent_signal_type, score, confidence_pct, reason = "HOLD", 0, 0.0, f"Error: {e}"

        return AgentSignal(
            agent_name=self.name,
            signal=agent_signal_type,
            score=float(score),
            confidence=round(confidence_pct / 100, 3),
            reason=reason,
            timestamp=now.isoformat(),
            next_action="วิเคราะห์รอบถัดไปใน 5 นาที (sync กับ 5m candle close)",
            price=price,
        )

    def _calculate_levels(self, entry_signal: str, ob: Optional[dict], liquidity: dict, price: float, cfg: dict):
        """
        entry = current price (close แท่ง 5m ล่าสุด)
        sl = ขอบ OB ฝั่งตรงข้าม entry +- sl_buffer
        tp1 = entry +- risk * tp1_rr
        tp2 = liquidity target ฝั่งเดียวกับ entry (BSL สำหรับ LONG, SSL สำหรับ SHORT)
        rr_ok = True ถ้า rr_tp2 >= min_tp2_rr

        คืน (levels: dict | None, rr_ok: bool)
        """
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

        levels = {
            "entry": round(price, 2),
            "sl": round(sl, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2) if tp2 is not None else None,
            "rr_tp1": rr_tp1,
            "rr_tp2": rr_tp2,
        }
        return levels, rr_ok

    def _find_relevant_fvg(self, active_fvgs: list[dict], entry_signal: str) -> Optional[dict]:
        """หา active FVG ที่ตรงกับทิศทาง entry_signal (ใช้แสดง context.fvg_fill_pct / htf_trend)"""
        if not active_fvgs:
            return None
        if entry_signal not in ("LONG", "SHORT"):
            return active_fvgs[-1]

        fvg_type = "BULL" if entry_signal == "LONG" else "BEAR"
        matching = [f for f in active_fvgs if f["type"] == fvg_type]
        return matching[-1] if matching else active_fvgs[-1]

    def _htf_trend(self, fvg: Optional[dict]) -> str:
        if fvg is None:
            return "NEUTRAL"
        return "BULLISH" if fvg["type"] == "BULL" else "BEARISH"

    def _build_reason(
        self,
        fvg: Optional[dict],
        session_name: str,
        stop_hunt: dict,
        displacement: dict,
        ob: Optional[dict],
    ) -> str:
        """สร้างข้อความสรุปเหตุผลแบบสั้นๆ จากแต่ละ detection step"""
        parts = []

        if fvg:
            trend = "Bullish" if fvg["type"] == "BULL" else "Bearish"
            parts.append(f"{trend} FVG 1H active ({fvg['fill_pct']:.0f}% fill)")
        else:
            parts.append("ไม่มี HTF FVG active")

        if stop_hunt["detected"]:
            parts.append(f"Stop Hunt ใน {session_name} session")
        else:
            parts.append("ไม่พบ Stop Hunt")

        if displacement["detected"]:
            disp_text = "Displacement"
            if displacement.get("msb"):
                disp_text += " + MSB"
            parts.append(disp_text)
        else:
            parts.append("ไม่พบ Displacement")

        parts.append("OB confirmed" if ob is not None else "ไม่พบ Order Block")

        return " | ".join(parts)
