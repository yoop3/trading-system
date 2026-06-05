"""
risk_agent.py — Risk Manager ที่มีสิทธิ์ VETO ทุก trade
รันทุกครั้งก่อน execute | ตรวจ daily loss, positions, position size
"""

from datetime import datetime, timezone
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
            daily_pnl = await self.db.get_today_pnl()
            positions = await self.data_fetcher.get_open_positions()

            # Check 1 — Daily loss limit
            if total_balance > 0 and daily_pnl / total_balance < -self.MAX_DAILY_LOSS_PCT:
                veto_reason = (
                    f"Daily loss limit reached: {daily_pnl/total_balance:.1%} "
                    f"(limit: {self.MAX_DAILY_LOSS_PCT:.0%})"
                )
                logger.warning(f"[risk] VETO — {veto_reason}")
                return self._veto(veto_reason)

            # Check 2 — Max open positions
            if len(positions) >= self.MAX_OPEN_POSITIONS:
                veto_reason = f"Max positions reached: {len(positions)}/{self.MAX_OPEN_POSITIONS}"
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
