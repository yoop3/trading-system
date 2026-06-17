"""
main.py — Orchestrator หลักของ AI Trading System (13 Agents)
BTC: 7 agents consensus (technical_btc, whale_btc, smc_btc, macro_btc, wyckoff_btc, sentiment, news)
XAU: 6 agents consensus (smc_xau, technical_xau, macro_xau, wyckoff_xau, news, sentiment)
Control: Risk Agent (รันทุกครั้งก่อน execute, แสดงสถานะ monitoring เสมอ)
รัน: python main.py | เปิด dashboard: http://localhost:8000
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

import uvicorn
from dotenv import load_dotenv
from loguru import logger

from agents.macro_btc_agent import MacroBTCAgent
from agents.macro_xau_agent import MacroXAUAgent
from agents.master_agent import MasterAgent
from agents.news_agent import NewsAgent
from agents.risk_agent import RiskAgent
from agents.sentiment_agent import SentimentAgent
from agents.smc_btc_agent import SMCBTCAgent
from agents.smc_xau_agent import SMCXAUAgent
from agents.technical_btc_agent import TechnicalBTCAgent
from agents.technical_xau_agent import TechnicalXAUAgent
from agents.whale_btc_agent import WhaleBTCAgent
from agents.wyckoff_btc_agent import WyckoffBTCAgent
from agents.wyckoff_xau_agent import WyckoffXAUAgent
from core.data_fetcher import DataFetcher
from core.database import Database
from core.executor import Executor
from core.position_monitor import PositionMonitor
from dashboard.server import DashboardServer


def setup_logging():
    """ตั้งค่า loguru"""
    log_level = os.getenv("LOG_LEVEL", "INFO")
    logger.remove()
    logger.add(sys.stderr, level=log_level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    logger.add("logs/trading_{time:YYYY-MM-DD}.log", rotation="1 day",
               retention="7 days", level="DEBUG", encoding="utf-8")


class TradingSystem:
    """
    Orchestrator รัน 13 agents ตาม schedule:
    BTC agents → BTC Master decision → Risk (เสมอ) → Execute
    XAU agents → XAU Master decision → Risk (เสมอ) → Execute
    """

    BTC_SYMBOL = "BTC/USDT:USDT"
    XAU_SYMBOL = "XAU/USDT:USDT"

    def __init__(self, data_fetcher: DataFetcher, db: Database, dashboard: DashboardServer):
        self.df = data_fetcher
        self.db = db
        self.dashboard = dashboard

        # --- SHARED agents (2) ---
        self.sentiment = SentimentAgent(data_fetcher, db)
        self.news      = NewsAgent(data_fetcher, db)

        # --- BTC agents (5 specialist) ---
        self.technical_btc = TechnicalBTCAgent(data_fetcher, db)
        self.whale_btc     = WhaleBTCAgent(data_fetcher, db)
        self.smc_btc       = SMCBTCAgent(data_fetcher, db)
        self.macro_btc     = MacroBTCAgent(data_fetcher, db)
        self.wyckoff_btc   = WyckoffBTCAgent(data_fetcher, db)

        # --- XAU agents (4 specialist) ---
        self.smc_xau       = SMCXAUAgent(data_fetcher, db)
        self.technical_xau = TechnicalXAUAgent(data_fetcher, db)
        self.macro_xau     = MacroXAUAgent(data_fetcher, db)
        self.wyckoff_xau   = WyckoffXAUAgent(data_fetcher, db)

        # --- Control ---
        self.risk    = RiskAgent(data_fetcher, db)
        self.master  = MasterAgent(data_fetcher, db, risk_agent=self.risk)
        self.executor = Executor(data_fetcher, db)
        self.position_monitor = PositionMonitor(data_fetcher, db)

        # signal dicts สำหรับ master
        self._btc_signals: dict = {}
        self._xau_signals: dict = {}

        # Schedule (วินาที)
        self._intervals = {
            # shared
            "sentiment":     15 * 60,
            "news":          30 * 60,
            # BTC
            "technical_btc": 5  * 60,
            "whale_btc":     15 * 60,
            "smc_btc":       5  * 60,
            "macro_btc":     4  * 60 * 60,
            "wyckoff_btc":   4  * 60 * 60,
            # XAU
            "smc_xau":       5  * 60,
            "technical_xau": 5  * 60,
            "macro_xau":     4  * 60 * 60,
            "wyckoff_xau":   4  * 60 * 60,
        }
        self._next_run = {k: 0.0 for k in self._intervals}

    # ──────────────────────────────────────────────
    # Generic agent runner
    # ──────────────────────────────────────────────

    async def _run_agent(self, name: str, agent, signals_dict: dict) -> bool:
        """รัน agent ถ้าถึงเวลา → เก็บ signal → dashboard update. คืน True ถ้าได้รัน"""
        now = asyncio.get_event_loop().time()
        if now < self._next_run[name]:
            return False
        self.dashboard.update_agent_status(name, "ANALYZING")
        signal = await agent.run()
        signals_dict[name] = signal
        self.dashboard.update_agent(name, signal)
        self._next_run[name] = now + self._intervals[name]
        return True

    # ──────────────────────────────────────────────
    # Reversal close helper
    # ──────────────────────────────────────────────

    async def _check_reversal_and_close(
        self,
        asset_symbol: str,
        asset_prefix: str,
        decision,
        current_positions: list,
        reversal_score_min: float,
        master_conf: float,
    ) -> bool:
        """
        ตรวจ 4 เงื่อนไขสำหรับปิด position เมื่อ signal กลับทาง:
        1. signal ตรงข้ามกับ position ปัจจุบัน
        2. signal ติดต่อกัน >= 3 รอบ (จาก master_decisions DB)
        3. |weighted_score| > reversal_score_min
        4. master_conf >= 60%
        คืน True ถ้าปิด position ไปแล้ว
        """
        if not current_positions:
            return False

        pos_side   = current_positions[0]["side"]
        new_signal = decision.signal
        opposite   = {"LONG": "SHORT", "SHORT": "LONG"}

        # Condition 1 — signal กลับทาง
        if new_signal != opposite.get(pos_side):
            return False

        # Condition 3 — score แรงพอ
        if abs(decision.total_score) <= reversal_score_min:
            logger.debug(
                f"[reversal] {asset_prefix} score {decision.total_score:+.1f} "
                f"≤ ±{reversal_score_min} — ยังไม่ปิด"
            )
            return False

        # Condition 4 — confidence >= 60%
        if master_conf < 0.60:
            logger.debug(f"[reversal] {asset_prefix} conf {master_conf:.0%} < 60% — ยังไม่ปิด")
            return False

        # Condition 2 — signal ติดต่อกัน >= 3 รอบ
        recent = await self.db.get_recent_master_decisions_for_asset(asset_prefix, limit=3)
        if len(recent) < 3:
            logger.debug(
                f"[reversal] {asset_prefix} history {len(recent)}/3 รอบ — ยังไม่ปิด"
            )
            return False

        expected = f"{asset_prefix}:{new_signal}"
        if not all(r["final_signal"] == expected for r in recent):
            logger.debug(
                f"[reversal] {asset_prefix} signal ไม่ consistent "
                f"{[r['final_signal'] for r in recent]}"
            )
            return False

        # ✅ ทุกเงื่อนไขผ่าน → ปิด position
        logger.warning(
            f"[reversal] {asset_prefix} CLOSING {pos_side} → {new_signal} "
            f"(score={decision.total_score:+.1f}, conf={master_conf:.0%}, 3 รอบติด)"
        )
        if self.executor.trading_enabled:
            await self.executor.close_position()
        else:
            await self.executor.close_paper_position(
                asset_symbol, reason=f"reversal {pos_side}→{new_signal}"
            )
        return True

    # ──────────────────────────────────────────────
    # BTC pipeline
    # ──────────────────────────────────────────────

    async def _run_btc_master(self):
        """Master BTC decision → Reversal check → Risk (เสมอ) → Execute"""
        if not self._btc_signals:
            return

        btc_decision = await self.master.decide_btc(self._btc_signals)
        self.dashboard.update_master_btc(btc_decision)

        current_positions = await self.db.get_open_trades(asset=self.BTC_SYMBOL)
        # confidence จาก weighted score — BTC threshold ±6 → score=12 คือ 100%
        master_conf = min(abs(btc_decision.total_score) / 12.0, 1.0)

        # Reversal check: ปิด position ถ้า signal กลับทางครบ 4 เงื่อนไข
        # BTC: reversal_score_min = 8 (threshold 6 + 2)
        closed_by_reversal = await self._check_reversal_and_close(
            asset_symbol=self.BTC_SYMBOL,
            asset_prefix="BTC",
            decision=btc_decision,
            current_positions=current_positions,
            reversal_score_min=8.0,
            master_conf=master_conf,
        )
        if closed_by_reversal:
            # reload positions หลังปิด ก่อน risk check
            current_positions = await self.db.get_open_trades(asset=self.BTC_SYMBOL)

        # Risk runs เสมอ — แม้แต่ตอน HOLD
        self.dashboard.update_agent_status("risk", "ANALYZING")
        risk_signal = await self.risk.check_asset(
            asset_symbol=self.BTC_SYMBOL,
            master_signal=btc_decision.signal,
            master_confidence=master_conf,
            master_score=btc_decision.total_score,
            current_positions=current_positions,
            is_xau=False,
        )
        self.dashboard.update_agent("risk", risk_signal)

        if btc_decision.signal == "HOLD":
            logger.info("[main] BTC Master: HOLD — ไม่เทรด")
            return

        if risk_signal.veto:
            logger.warning(f"[main] BTC Risk VETO: {risk_signal.reason}")
            return

        size     = risk_signal.recommended_size or 0.001
        leverage = risk_signal.recommended_leverage or 3

        try:
            df_1h = await self.df.get_ohlcv("1h", limit=50, symbol=self.BTC_SYMBOL)
            from core.indicators import Indicators
            ind = Indicators()
            df_1h = ind.calculate_all(df_1h)
            latest = ind.get_latest(df_1h)
            price = float(latest.get("close", 0))
            atr   = float(latest.get("ATR_14", price * 0.01))
        except Exception as e:
            logger.error(f"[main] BTC ATR fetch failed: {e}")
            return

        if btc_decision.signal == "LONG":
            tp, sl = self.executor.calculate_tp_sl("LONG", price, atr)
            result = await self.executor.open_long(
                size, leverage, tp, sl,
                reason=btc_decision.reasoning, asset=self.BTC_SYMBOL,
            )
        else:
            tp, sl = self.executor.calculate_tp_sl("SHORT", price, atr)
            result = await self.executor.open_short(
                size, leverage, tp, sl,
                reason=btc_decision.reasoning, asset=self.BTC_SYMBOL,
            )

        if result:
            logger.success(
                f"[main] BTC trade: {btc_decision.signal} size={size} @ {price:.0f} "
                f"TP={tp:.0f} SL={sl:.0f}"
            )

    # ──────────────────────────────────────────────
    # XAU pipeline
    # ──────────────────────────────────────────────

    async def _run_xau_master(self):
        """Master XAU decision → Reversal check → Risk (เสมอ) → Execute"""
        if not self._xau_signals:
            return

        xau_decision = await self.master.decide_xau(self._xau_signals)
        self.dashboard.update_master_xau(xau_decision)

        current_positions = await self.db.get_open_trades(asset=self.XAU_SYMBOL)
        # confidence จาก weighted score — XAU threshold ±5 → score=10 คือ 100%
        master_conf = min(abs(xau_decision.total_score) / 10.0, 1.0)

        # Reversal check: XAU reversal_score_min = 7 (threshold 5 + 2)
        closed_by_reversal = await self._check_reversal_and_close(
            asset_symbol=self.XAU_SYMBOL,
            asset_prefix="XAU",
            decision=xau_decision,
            current_positions=current_positions,
            reversal_score_min=7.0,
            master_conf=master_conf,
        )
        if closed_by_reversal:
            current_positions = await self.db.get_open_trades(asset=self.XAU_SYMBOL)

        # Risk runs เสมอ — แม้แต่ตอน HOLD
        risk_signal = await self.risk.check_asset(
            asset_symbol=self.XAU_SYMBOL,
            master_signal=xau_decision.signal,
            master_confidence=master_conf,
            master_score=xau_decision.total_score,
            current_positions=current_positions,
            is_xau=True,
        )

        if xau_decision.signal == "HOLD":
            logger.info("[main] XAU Master: HOLD — ไม่เทรด")
            return

        if risk_signal.veto:
            logger.warning(f"[main] XAU Risk VETO: {risk_signal.reason}")
            return

        # ใช้ SMC XAU levels ถ้ามี ไม่งั้นใช้ ATR
        smc_out = self.smc_xau.last_smc_output
        if smc_out and smc_out.get("levels"):
            levels = smc_out["levels"]
            tp = levels["tp1"]
            sl = levels["sl"]
        else:
            try:
                df_1h = await self.df.get_ohlcv("1h", limit=50, symbol=self.XAU_SYMBOL)
                from core.indicators import Indicators
                ind = Indicators()
                df_1h = ind.calculate_all(df_1h)
                latest = ind.get_latest(df_1h)
                price = float(latest.get("close", 0))
                atr   = float(latest.get("ATR_14", price * 0.005))
                tp, sl = self.executor.calculate_tp_sl(xau_decision.signal, price, atr)
            except Exception as e:
                logger.error(f"[main] XAU ATR fetch failed: {e}")
                return

        size     = risk_signal.recommended_size or 0.001
        leverage = risk_signal.recommended_leverage or 1

        if xau_decision.signal == "LONG":
            result = await self.executor.open_long(
                size, leverage, tp, sl,
                reason=xau_decision.reasoning, asset=self.XAU_SYMBOL,
            )
        else:
            result = await self.executor.open_short(
                size, leverage, tp, sl,
                reason=xau_decision.reasoning, asset=self.XAU_SYMBOL,
            )

        if result:
            logger.success(
                f"[main] XAU trade: {xau_decision.signal} size={size} | TP={tp} SL={sl}"
            )

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    async def _record_balance(self):
        try:
            balance = await self.df.get_balance()
            positions = await self.df.get_open_positions()
            upnl = sum(float(p.get("unrealizedPnl", 0)) for p in positions)
            await self.db.save_balance(balance["total"], upnl)
            logger.debug(f"Balance snapshot: {balance['total']:.2f} USDT (uPnL: {upnl:.2f})")
        except Exception as e:
            logger.error(f"[main] balance snapshot error: {e}")

    # ──────────────────────────────────────────────
    # Main Loop
    # ──────────────────────────────────────────────

    async def run_loop(self):
        logger.info("Trading loop started (13-agent BTC+XAU system)")
        last_balance_snapshot = 0.0
        balance_interval = 60 * 60

        while True:
            try:
                now = asyncio.get_event_loop().time()

                # ตรวจ paper trades TP/SL
                await self.position_monitor.check()

                # --- SHARED agents ---
                await self._run_agent("sentiment", self.sentiment, self._btc_signals)
                self._xau_signals["sentiment"] = self._btc_signals.get("sentiment")  # reuse
                await self._run_agent("news", self.news, self._btc_signals)
                self._xau_signals["news"] = self._btc_signals.get("news")  # reuse

                # --- BTC specialist agents ---
                ran_btc_tech = await self._run_agent("technical_btc", self.technical_btc, self._btc_signals)
                await self._run_agent("whale_btc",   self.whale_btc,   self._btc_signals)
                await self._run_agent("smc_btc",     self.smc_btc,     self._btc_signals)
                await self._run_agent("macro_btc",   self.macro_btc,   self._btc_signals)
                await self._run_agent("wyckoff_btc", self.wyckoff_btc, self._btc_signals)

                # BTC Master รันหลัง technical_btc (ทุก 5 นาที)
                if ran_btc_tech:
                    await self._run_btc_master()

                # --- XAU specialist agents ---
                ran_xau_smc  = await self._run_agent("smc_xau",       self.smc_xau,       self._xau_signals)
                ran_xau_tech = await self._run_agent("technical_xau", self.technical_xau, self._xau_signals)
                await self._run_agent("macro_xau",   self.macro_xau,   self._xau_signals)
                await self._run_agent("wyckoff_xau", self.wyckoff_xau, self._xau_signals)

                if ran_xau_smc or ran_xau_tech:
                    await self._run_xau_master()

                # Balance snapshot ทุกชั่วโมง
                if now - last_balance_snapshot >= balance_interval:
                    await self._record_balance()
                    last_balance_snapshot = now

            except Exception as e:
                logger.error(f"[main] trading loop error: {e}")

            await asyncio.sleep(30)


# ──────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────

async def main():
    load_dotenv()
    os.makedirs("logs", exist_ok=True)
    setup_logging()

    logger.info("=" * 60)
    logger.info("AI Trading System Starting (13 Agents — BTC+XAU)")
    logger.info("BTC/USDT:USDT + XAU/USDT:USDT")
    logger.info("TESTNET/PAPER MODE — ไม่ใช้เงินจริง")
    logger.info("=" * 60)

    data_fetcher = DataFetcher()
    logger.info("DataFetcher initialized")

    db = Database()
    await db.connect()
    logger.info("Database connected")

    dashboard = DashboardServer()
    dashboard.set_dependencies(db, data_fetcher)

    trading = TradingSystem(data_fetcher, db, dashboard)

    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    logger.info(f"Dashboard: http://localhost:{port}")

    config = uvicorn.Config(
        dashboard.app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    await asyncio.gather(
        server.serve(),
        trading.run_loop(),
        dashboard.broadcast_loop(),
    )


if __name__ == "__main__":
    print("AI Trading System Starting (13 Agents — BTC+XAU)")
    print("Dashboard: http://localhost:8000")
    print("PAPER MODE — ไม่ใช้เงินจริง")
    print("")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSystem stopped by user")
