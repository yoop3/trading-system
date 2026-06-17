"""
risk_agent.py — Risk Manager ที่มีสิทธิ์ VETO ทุก trade
รันทุกครั้งก่อน execute | ตรวจ daily loss, positions, position size
Per-asset: max 1 pos/asset, daily loss -5%, confidence<60% VETO
XAU: VETO เพิ่มเติมถ้าอยู่ใกล้ major US news window
"""

import os
from datetime import datetime, timezone
from typing import Optional
from loguru import logger

from agents.base_agent import BaseAgent, AgentSignal
from core.indicators import Indicators


class RiskAgent(BaseAgent):
    """
    ตรวจสอบ risk ก่อนทุก trade ถ้าผิดเงื่อนไขข้อไหนก็ VETO ทันที
    ยังคำนวณ position size ที่เหมาะสมด้วย
    """

    MAX_DAILY_LOSS_PCT = 0.05      # หยุดถ้าขาดทุน 5% ในวันนั้น
    MIN_CONFIDENCE = 0.60          # confidence ต่ำกว่า 60% → VETO
    MAX_LEVERAGE = 5               # leverage สูงสุด
    RISK_PER_TRADE_PCT = 0.02      # เสี่ยงต่อ trade ไม่เกิน 2% ของ balance
    MAX_OPEN_POSITIONS = 1         # มี position เปิดพร้อมกันได้แค่ 1
    MIN_ATR_RATIO = 0.003          # ATR/price ต้องสูงพอ จึงเทรด (volatility check)

    def __init__(self, data_fetcher, db):
        super().__init__("risk", data_fetcher, db)
        self.indicators = Indicators()

    async def check(
        self,
        master_signal: str,
        master_confidence: float,
        master_score: float,
    ) -> AgentSignal:
        """
        ตรวจ risk สำหรับ signal ที่ Master Agent ส่งมา
        คืน AgentSignal ที่มี veto=True ถ้าผ่านไม่ได้ หรือ signal='APPROVED' พร้อม size
        """
        return await self._check_risk(master_signal, master_confidence, master_score)

    async def analyze(self) -> AgentSignal:
        """implement abstract method — ใช้ check() แทนในการทำงานจริง"""
        return await self._check_risk("HOLD", 0.0, 0.0)

    async def check_smc(self, smc_output: Optional[dict], current_positions: list) -> AgentSignal:
        """
        ตรวจ risk สำหรับผลลัพธ์ของ SMC Agent (SMC_Agent_Spec.md - Risk Agent Integration)
        current_positions: open trades/positions ของ asset เดียวกับ smc_output['asset'] เท่านั้น
        (ปัจจุบัน Master/Executor ยังไม่ track asset แยก — ผู้เรียกต้อง filter list นี้เอง)

        VETO ถ้า: NO_SETUP, |score| ต่ำกว่า min_score_to_signal, RR tp2 ไม่พอ,
        หรือ asset นี้มี position เปิดอยู่แล้ว (>= MAX_OPEN_POSITIONS)
        ถ้าผ่านหมด -> APPROVED พร้อม levels (entry/sl/tp1/tp2) และ recommended size/leverage
        """
        if not smc_output:
            return self._veto("SMC: ไม่มีผลลัพธ์ (analyze error)")

        asset = smc_output.get("asset", "?")
        signal = smc_output.get("signal", "NO_SETUP")
        score = smc_output.get("score", 0)
        confidence = smc_output.get("confidence", 0.0)
        levels = smc_output.get("levels")

        # Check 1 — ไม่มี setup
        if signal == "NO_SETUP":
            return self._veto(f"SMC [{asset}]: NO_SETUP")

        # Check 2 — score ต่ำกว่า threshold (ค่า threshold มาจาก config ของ SMC Agent ตัวนั้นๆ)
        min_score = smc_output.get("min_score_to_signal", 2)
        if abs(score) < min_score:
            return self._veto(f"SMC [{asset}]: |score|={abs(score)} < {min_score}")

        # Check 3 — RR ไม่พอ
        min_tp2_rr = smc_output.get("min_tp2_rr", 1.5)
        rr_tp2 = levels.get("rr_tp2", 0) if levels else 0
        if not levels or rr_tp2 < min_tp2_rr:
            return self._veto(f"SMC [{asset}]: RR tp2={rr_tp2} < {min_tp2_rr}")

        # Check 4 — asset นี้มี position เปิดอยู่แล้ว / เต็ม quota
        if len(current_positions) >= self.MAX_OPEN_POSITIONS:
            return self._veto(
                f"SMC [{asset}]: position เปิดอยู่แล้ว "
                f"({len(current_positions)}/{self.MAX_OPEN_POSITIONS})"
            )

        # คำนวณ position size จาก SL distance (เหมือน _check_risk แต่ใช้ SL ของ SMC levels)
        recommended_size = 0.001
        recommended_leverage = min(self.MAX_LEVERAGE, max(1, int(confidence / 100 * self.MAX_LEVERAGE)))
        try:
            balance = await self.data_fetcher.get_balance()
            total_balance = balance.get("total", 0)
            trading_enabled = os.getenv("TRADING_ENABLED", "false").lower() == "true"
            if total_balance == 0 and not trading_enabled:
                total_balance = float(os.getenv("PAPER_BALANCE", "0"))

            sl_distance = abs(levels["entry"] - levels["sl"])
            if sl_distance > 0 and total_balance > 0:
                risk_amount = total_balance * self.RISK_PER_TRADE_PCT
                recommended_size = max(0.001, round(risk_amount / sl_distance, 3))
        except Exception as e:
            logger.warning(f"[risk] SMC size calc failed (ใช้ minimum size): {e}")

        logger.info(
            f"[risk] SMC [{asset}] APPROVED — {signal} score={score} "
            f"size={recommended_size} leverage={recommended_leverage}x | levels={levels}"
        )
        return AgentSignal(
            agent_name=self.name,
            signal="APPROVED",
            score=float(score),
            confidence=confidence / 100,
            reason=f"SMC [{asset}] risk checks passed | {signal} | levels={levels}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            next_action=f"Execute SMC {signal} ({asset})",
            veto=False,
            recommended_size=recommended_size,
            recommended_leverage=recommended_leverage,
        )

    async def _check_risk(
        self,
        master_signal: str,
        master_confidence: float,
        master_score: float,
    ) -> AgentSignal:
        veto_reason = ""
        recommended_size = 0.0
        recommended_leverage = 3

        try:
            # ดึงข้อมูลที่ต้องการ
            balance = await self.data_fetcher.get_balance()
            total_balance = balance.get("total", 0)
            trading_enabled = os.getenv("TRADING_ENABLED", "false").lower() == "true"
            if total_balance == 0 and not trading_enabled:
                total_balance = float(os.getenv("PAPER_BALANCE", "0"))
            daily_pnl = await self.db.get_today_pnl()
            if trading_enabled:
                open_count = len(await self.data_fetcher.get_open_positions())
            else:
                open_count = len(await self.db.get_open_trades(asset=self.data_fetcher.symbol))

            # Check 1 — Daily loss limit
            if total_balance > 0 and daily_pnl / total_balance < -self.MAX_DAILY_LOSS_PCT:
                veto_reason = (
                    f"Daily loss limit reached: {daily_pnl/total_balance:.1%} "
                    f"(limit: {self.MAX_DAILY_LOSS_PCT:.0%})"
                )
                logger.warning(f"[risk] VETO — {veto_reason}")
                return self._veto(veto_reason)

            # Check 2 — Max open positions
            if open_count >= self.MAX_OPEN_POSITIONS:
                veto_reason = f"Max positions reached: {open_count}/{self.MAX_OPEN_POSITIONS}"
                logger.warning(f"[risk] VETO — {veto_reason}")
                return self._veto(veto_reason)

            # Check 3 — Minimum confidence
            if master_confidence < self.MIN_CONFIDENCE and master_signal != "HOLD":
                veto_reason = (
                    f"Confidence too low: {master_confidence:.0%} "
                    f"(min: {self.MIN_CONFIDENCE:.0%})"
                )
                logger.warning(f"[risk] VETO — {veto_reason}")
                return self._veto(veto_reason)

            # Check 4 — Market volatility (ATR ratio)
            try:
                df_1h = await self.data_fetcher.get_ohlcv("1h", limit=50)
                df_1h = self.indicators.calculate_all(df_1h)
                ind = self.indicators.get_latest(df_1h)
                price = float(ind.get("close", 0))
                atr = float(ind.get("ATR_14", 0))

                if price > 0 and atr > 0:
                    atr_ratio = atr / price
                    if atr_ratio < self.MIN_ATR_RATIO:
                        veto_reason = (
                            f"Volatility too low: ATR/price={atr_ratio:.4f} "
                            f"(min: {self.MIN_ATR_RATIO})"
                        )
                        logger.warning(f"[risk] VETO — {veto_reason}")
                        return self._veto(veto_reason)

                    # คำนวณ position size ที่ปลอดภัย
                    # SL = ATR × 1.0, Risk = balance × RISK_PCT
                    sl_distance = atr * 1.0
                    if sl_distance > 0 and total_balance > 0:
                        risk_amount = total_balance * self.RISK_PER_TRADE_PCT
                        # size in ETH = risk_amount / sl_distance
                        recommended_size = round(risk_amount / sl_distance, 4)
                        recommended_size = max(0.001, recommended_size)

                    # leverage: ปรับตาม confidence (สูงสุด MAX_LEVERAGE)
                    recommended_leverage = min(
                        self.MAX_LEVERAGE,
                        max(1, int(master_confidence * self.MAX_LEVERAGE)),
                    )

            except Exception as e:
                logger.warning(f"[risk] ATR check failed (skipped): {e}")
                price = 0.0
                recommended_size = 0.001  # minimum size

        except Exception as e:
            logger.error(f"RiskAgent check error: {e}")
            return self._veto(f"Risk check error: {str(e)[:100]}")

        # ผ่านทุก check → APPROVED
        logger.info(
            f"[risk] APPROVED — size={recommended_size} ETH, "
            f"leverage={recommended_leverage}x"
        )
        return AgentSignal(
            agent_name=self.name,
            signal="APPROVED",
            score=0.0,
            confidence=master_confidence,
            reason=f"All risk checks passed | size={recommended_size} ETH",
            timestamp=datetime.now(timezone.utc).isoformat(),
            next_action="Execute trade",
            veto=False,
            recommended_size=recommended_size,
            recommended_leverage=recommended_leverage,
        )

    async def check_asset(
        self,
        asset_symbol: str,
        master_signal: str,
        master_confidence: float,
        master_score: float,
        current_positions: list,
        is_xau: bool = False,
    ) -> AgentSignal:
        """
        ตรวจ risk สำหรับ Master Agent decision (BTC หรือ XAU)
        asset_symbol: ccxt symbol เช่น "BTC/USDT:USDT"
        is_xau: True → เพิ่ม XAU news VETO
        """
        now = datetime.now(timezone.utc)
        asset_label = asset_symbol.split("/")[0]

        # Check 1 — HOLD → ไม่ต้องทำอะไร
        if master_signal == "HOLD":
            return self._veto(f"{asset_label}: HOLD signal")

        # Check 2 — Confidence < 60%
        if master_confidence < self.MIN_CONFIDENCE:
            return self._veto(
                f"{asset_label}: confidence {master_confidence:.0%} < {self.MIN_CONFIDENCE:.0%}"
            )

        # Check 3 — Position limit per asset
        if len(current_positions) >= self.MAX_OPEN_POSITIONS:
            return self._veto(
                f"{asset_label}: มี position เปิดอยู่แล้ว "
                f"({len(current_positions)}/{self.MAX_OPEN_POSITIONS})"
            )

        # Check 4 — XAU news VETO
        if is_xau and os.getenv("XAU_NEWS_AVOIDANCE", "false").lower() == "true":
            news_times = [(8, 30), (13, 30), (14, 0), (18, 0)]
            current_min = now.hour * 60 + now.minute
            for h, m in news_times:
                if abs(current_min - (h * 60 + m)) <= 30:
                    return self._veto(f"XAU near major US news ({now.strftime('%H:%M')} UTC)")

        # Check 5 — Daily loss limit (ทุก asset รวม)
        try:
            daily_pnl = await self.db.get_today_pnl()
            trading_enabled = os.getenv("TRADING_ENABLED", "false").lower() == "true"
            balance = await self.data_fetcher.get_balance()
            total_balance = balance.get("total", 0)
            if total_balance == 0 and not trading_enabled:
                total_balance = float(os.getenv("PAPER_BALANCE", "0"))
            if total_balance > 0 and daily_pnl / total_balance < -self.MAX_DAILY_LOSS_PCT:
                return self._veto(
                    f"{asset_label}: daily loss {daily_pnl/total_balance:.1%} "
                    f"≤ -{self.MAX_DAILY_LOSS_PCT:.0%}"
                )

            # คำนวณ position size (2% risk, SL = ATR × 1.5)
            recommended_size = 0.001
            recommended_leverage = min(
                self.MAX_LEVERAGE,
                max(1, int(master_confidence * self.MAX_LEVERAGE)),
            )
            try:
                df_1h = await self.data_fetcher.get_ohlcv("1h", limit=50, symbol=asset_symbol)
                from core.indicators import Indicators
                ind = Indicators()
                df_1h = ind.calculate_all(df_1h)
                latest = ind.get_latest(df_1h)
                price_now = float(latest.get("close", 0))
                atr = float(latest.get("ATR_14", 0))
                if atr > 0 and total_balance > 0:
                    sl_dist = atr * 1.5
                    risk_amt = total_balance * self.RISK_PER_TRADE_PCT
                    recommended_size = max(0.001, round(risk_amt / sl_dist, 4))
            except Exception as e:
                logger.warning(f"[risk] {asset_label} size calc failed: {e}")

        except Exception as e:
            logger.error(f"[risk] check_asset error ({asset_label}): {e}")
            return self._veto(f"Risk check error: {str(e)[:80]}")

        logger.info(
            f"[risk] {asset_label} APPROVED — {master_signal} conf={master_confidence:.0%} "
            f"size={recommended_size} leverage={recommended_leverage}x"
        )
        return AgentSignal(
            agent_name=self.name,
            signal="APPROVED",
            score=float(master_score),
            confidence=master_confidence,
            reason=f"{asset_label} risk checks passed | {master_signal}",
            timestamp=now.isoformat(),
            next_action=f"Execute {master_signal} ({asset_label})",
            veto=False,
            recommended_size=recommended_size,
            recommended_leverage=recommended_leverage,
        )

    def _veto(self, reason: str) -> AgentSignal:
        """สร้าง VETO signal"""
        return AgentSignal(
            agent_name=self.name,
            signal="VETO",
            score=0.0,
            confidence=1.0,
            reason=reason,
            timestamp=datetime.now(timezone.utc).isoformat(),
            next_action="รอเงื่อนไข risk ผ่านก่อนถึงเทรดได้",
            veto=True,
        )
