"""
main.py — Orchestrator หลักของ AI Trading System (7 Agents)
รัน: python main.py
เปิด dashboard: http://localhost:8000
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

import uvicorn
from dotenv import load_dotenv
from loguru import logger

from agents.macro_agent import MacroAgent
from agents.master_agent import MasterAgent
from agents.news_agent import NewsAgent
from agents.risk_agent import RiskAgent
from agents.sentiment_agent import SentimentAgent
from agents.smc_agent.config import BTC_CONFIG, SMC_CONFIG
from agents.smc_agent.smc_agent import SMCAgent
from agents.technical_agent import TechnicalAgent
from agents.whale_agent import WhaleAgent
from core.data_fetcher import DataFetcher
from core.database import Database
from core.executor import Executor
from core.position_monitor import PositionMonitor
from core.trade_grader import grade_trade
from dashboard.server import DashboardServer


# ---------- Config ----------
def setup_logging():
    """ตั้งค่า loguru สำหรับ log ไฟล์และ console"""
    log_level = os.getenv("LOG_LEVEL", "INFO")
    logger.remove()
    logger.add(sys.stderr, level=log_level, colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
    logger.add("logs/trading_{time:YYYY-MM-DD}.log", rotation="1 day",
               retention="7 days", level="DEBUG", encoding="utf-8")


# ---------- Trading Loop ----------
class TradingSystem:
    """
    Orchestrator ที่รัน agent ทั้งหมดตาม schedule
    และส่ง state ไปยัง dashboard
    """

    def __init__(self, data_fetcher: DataFetcher, db: Database, dashboard: DashboardServer):
        self.df = data_fetcher
        self.db = db
        self.dashboard = dashboard

        # สร้าง agents ทั้งหมด
        self.technical = TechnicalAgent(data_fetcher, db)
        self.macro = MacroAgent(data_fetcher, db)
        self.sentiment = SentimentAgent(data_fetcher, db)
        self.news = NewsAgent(data_fetcher, db)
        self.whale = WhaleAgent(data_fetcher, db)
        self.risk = RiskAgent(data_fetcher, db)
        self.master = MasterAgent(data_fetcher, db, risk_agent=self.risk)
        self.smc_xau = SMCAgent(data_fetcher, db, config=SMC_CONFIG, name="smc_xau")
        self.smc_btc = SMCAgent(data_fetcher, db, config=BTC_CONFIG, name="smc_btc")
        self.executor = Executor(data_fetcher, db)
        self.position_monitor = PositionMonitor(data_fetcher, db)

        # เก็บ signals ล่าสุดของแต่ละ agent
        self._signals: dict = {}

        # ตั้ง interval ของแต่ละ agent (วินาที)
        self._intervals = {
            "technical": 5 * 60,    # ทุก 5 นาที
            "sentiment": 15 * 60,   # ทุก 15 นาที
            "whale":     15 * 60,   # ทุก 15 นาที
            "news":      30 * 60,   # ทุก 30 นาที
            "macro":     4 * 60 * 60,  # ทุก 4 ชั่วโมง
            "smc_xau":   5 * 60,    # ทุก 5 นาที (sync กับ LTF 5m candle)
            "smc_btc":   5 * 60,    # ทุก 5 นาที (sync กับ LTF 5m candle)
        }
        # ตั้งให้รอบแรกรันทันที
        self._next_run = {k: 0.0 for k in self._intervals}

    async def _run_agent_if_due(self, name: str, agent) -> bool:
        """รัน agent ถ้าถึงเวลาแล้ว คืน True ถ้ารัน"""
        now = asyncio.get_event_loop().time()
        if now >= self._next_run[name]:
            self.dashboard.update_agent_status(name, "ANALYZING")
            signal = await agent.run()
            self._signals[name] = signal
            self.dashboard.update_agent(name, signal)
            self._next_run[name] = now + self._intervals[name]
            return True
        return False

    async def _run_master(self):
        """รัน Master Agent หลัง technical agent ทุกรอบ"""
        if not self._signals:
            return

        decision = await self.master.decide(self._signals)
        self.dashboard.update_master(decision)

        # ถ้า HOLD → ไม่ต้องทำอะไร
        if decision.signal == "HOLD":
            logger.info("[main] Master: HOLD — ไม่เทรด")
            return

        # ผ่าน Risk Agent ก่อนเสมอ
        master_conf = max(
            (s.confidence for s in self._signals.values()), default=0.0
        )
        self.dashboard.update_agent_status("risk", "ANALYZING")
        risk_signal = await self.risk.check(
            master_signal=decision.signal,
            master_confidence=master_conf,
            master_score=decision.total_score,
        )
        self.dashboard.update_agent("risk", risk_signal)

        if risk_signal.veto:
            logger.warning(f"[main] Risk VETO: {risk_signal.reason}")
            return

        # Execute trade
        size = risk_signal.recommended_size or 0.001
        leverage = risk_signal.recommended_leverage or 3

        try:
            df_1h = await self.df.get_ohlcv("1h", limit=50)
            from core.indicators import Indicators
            ind = Indicators()
            df_1h = ind.calculate_all(df_1h)
            latest = ind.get_latest(df_1h)
            price = float(latest.get("close", 0))
            atr = float(latest.get("ATR_14", price * 0.01))
        except Exception as e:
            logger.error(f"[main] ATR fetch for TP/SL failed: {e}")
            return

        # ให้เกรด A/B/C/D กับ trade นี้ (ดูเกณฑ์ใน docs/TRADE_GRADING.md)
        atr_ratio = atr / price if price > 0 else 0.0
        grade, grade_breakdown = grade_trade(
            weight_ratio=decision.weight_ratio,
            total_score=decision.total_score,
            confidence=master_conf,
            atr_ratio=atr_ratio,
        )
        logger.info(f"[main] Trade grade: {grade} ({grade_breakdown})")

        if decision.signal == "LONG":
            tp, sl = self.executor.calculate_tp_sl("LONG", price, atr)
            result = await self.executor.open_long(
                size, leverage, tp, sl, reason=decision.reasoning, grade=grade, grade_detail=grade_breakdown
            )
        else:
            tp, sl = self.executor.calculate_tp_sl("SHORT", price, atr)
            result = await self.executor.open_short(
                size, leverage, tp, sl, reason=decision.reasoning, grade=grade, grade_detail=grade_breakdown
            )

        if result:
            logger.success(
                f"[main] Trade executed: {decision.signal} "
                f"size={size} ETH @ {price} | TP={tp} SL={sl}"
            )

    async def _run_smc_if_due(self, key: str, agent: SMCAgent, cfg: dict):
        """
        รัน SMC Agent (key = "smc_xau"/"smc_btc") ตาม schedule แยกจาก consensus หลัก (ETHUSDT)
        ไม่เก็บเข้า self._signals เพราะเป็นคนละ asset กับ Master Agent
        """
        now = asyncio.get_event_loop().time()
        if now < self._next_run[key]:
            return

        self.dashboard.update_agent_status(key, "ANALYZING")
        signal = await agent.run()
        self.dashboard.update_agent(key, signal)
        self._next_run[key] = now + self._intervals[key]

        await self._handle_smc_signal(agent, cfg)

    async def _handle_smc_signal(self, agent: SMCAgent, cfg: dict):
        """ส่งผลลัพธ์ SMC Agent ผ่าน Risk Agent แล้ว execute ถ้า APPROVED"""
        smc_output = agent.last_smc_output
        if not smc_output:
            return

        current_positions = await self.db.get_open_trades(asset=cfg["symbol"])
        risk_signal = await self.risk.check_smc(smc_output, current_positions)

        if risk_signal.veto:
            logger.debug(f"[main] SMC [{cfg['asset']}] risk: {risk_signal.reason}")
            return

        levels = smc_output["levels"]
        side = smc_output["signal"]
        size = risk_signal.recommended_size or 0.001
        leverage = risk_signal.recommended_leverage or 1

        if side == "LONG":
            result = await self.executor.open_long(
                size, leverage, levels["tp1"], levels["sl"],
                reason=smc_output["reason"], asset=cfg["symbol"],
            )
        else:
            result = await self.executor.open_short(
                size, leverage, levels["tp1"], levels["sl"],
                reason=smc_output["reason"], asset=cfg["symbol"],
            )

        if result:
            logger.success(
                f"[main] SMC trade executed: {side} ({cfg['asset']}) "
                f"size={size} leverage={leverage}x | levels={levels}"
            )

    async def _record_balance(self):
        """บันทึก balance snapshot ทุกชั่วโมง"""
        try:
            balance = await self.df.get_balance()
            positions = await self.df.get_open_positions()
            upnl = sum(float(p.get("unrealizedPnl", 0)) for p in positions)
            await self.db.save_balance(balance["total"], upnl)
            logger.debug(f"Balance snapshot: {balance['total']:.2f} USDT (uPnL: {upnl:.2f})")
        except Exception as e:
            logger.error(f"[main] balance snapshot error: {e}")

    async def run_loop(self):
        """
        Main trading loop รันตลอด
        Technical agent รันทุก 5 นาที → trigger master decision
        """
        logger.info("Trading loop started")
        last_balance_snapshot = 0.0
        balance_interval = 60 * 60  # ทุก 1 ชั่วโมง

        while True:
            try:
                now = asyncio.get_event_loop().time()

                # ตรวจ paper trades ว่าโดน TP/SL ไหม (ทุก loop = ทุก 30 วินาที)
                await self.position_monitor.check()

                # รัน agent ตาม schedule
                ran_technical = await self._run_agent_if_due("technical", self.technical)
                await self._run_agent_if_due("sentiment", self.sentiment)
                await self._run_agent_if_due("whale", self.whale)
                await self._run_agent_if_due("news", self.news)
                await self._run_agent_if_due("macro", self.macro)

                # Master รันหลัง technical เสมอ
                if ran_technical:
                    await self._run_master()

                # SMC Agents (XAUUSDT, BTCUSDT) — รันแยก schedule ของตัวเอง
                await self._run_smc_if_due("smc_xau", self.smc_xau, SMC_CONFIG)
                await self._run_smc_if_due("smc_btc", self.smc_btc, BTC_CONFIG)

                # Balance snapshot ทุกชั่วโมง
                if now - last_balance_snapshot >= balance_interval:
                    await self._record_balance()
                    last_balance_snapshot = now

            except Exception as e:
                logger.error(f"[main] trading loop error: {e}")

            # รอ 30 วินาทีก่อนเช็คอีกครั้ง
            await asyncio.sleep(30)


# ---------- Entry Point ----------
async def main():
    # โหลด .env
    load_dotenv()

    # สร้าง logs directory
    os.makedirs("logs", exist_ok=True)
    setup_logging()

    logger.info("=" * 60)
    logger.info("🚀 AI Trading System Starting (7 Agents)")
    logger.info("📍 Symbol: " + os.getenv("TRADING_SYMBOL", "ETH/USDT:USDT"))
    logger.info("⚠️  TESTNET MODE — ไม่ใช้เงินจริง")
    logger.info("=" * 60)

    # เชื่อม Binance Testnet
    data_fetcher = DataFetcher()
    logger.info("DataFetcher initialized")

    # สร้าง SQLite database
    db = Database()
    await db.connect()
    logger.info("Database connected")

    # สร้าง dashboard server
    dashboard = DashboardServer()
    dashboard.set_dependencies(db, data_fetcher)

    # สร้าง trading system
    trading = TradingSystem(data_fetcher, db, dashboard)

    # Port สำหรับ dashboard
    port = int(os.getenv("DASHBOARD_PORT", "8000"))

    logger.info(f"📊 Dashboard: http://localhost:{port}")
    logger.info("Starting all services...")

    # รัน trading loop, dashboard broadcast loop, และ uvicorn พร้อมกัน
    config = uvicorn.Config(
        dashboard.app,
        host="0.0.0.0",
        port=port,
        log_level="warning",  # ลด noise จาก uvicorn
    )
    server = uvicorn.Server(config)

    await asyncio.gather(
        server.serve(),
        trading.run_loop(),
        dashboard.broadcast_loop(),
    )


if __name__ == "__main__":
    print("🚀 AI Trading System Starting...")
    print("📊 Dashboard: http://localhost:8000")
    print("⚠️  TESTNET MODE — ไม่ใช้เงินจริง")
    print("")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSystem stopped by user")
