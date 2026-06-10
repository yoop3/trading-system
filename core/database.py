"""
database.py — SQLite database สำหรับบันทึก signals, trades, balance history
ใช้ aiosqlite สำหรับ async operations
"""

import aiosqlite
import os
from datetime import datetime, date
from loguru import logger
from typing import Optional


DB_PATH = os.getenv("DB_PATH", "trading.db")


class Database:
    """
    จัดการ SQLite database สำหรับระบบ trading
    4 tables: agent_signals, master_decisions, trades, balance_history
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        """เปิด connection และสร้าง tables ถ้ายังไม่มี"""
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._create_tables()
        logger.info(f"Database connected: {self.db_path}")

    async def close(self):
        """ปิด connection"""
        if self._conn:
            await self._conn.close()
            logger.info("Database connection closed")

    async def _create_tables(self):
        """สร้าง 4 tables ถ้ายังไม่มีอยู่"""
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS agent_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                signal TEXT NOT NULL,
                score REAL NOT NULL,
                confidence REAL NOT NULL,
                reason TEXT,
                price REAL
            );

            CREATE TABLE IF NOT EXISTS master_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                final_signal TEXT NOT NULL,
                total_score REAL NOT NULL,
                llm_reasoning TEXT,
                was_executed INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                tp_price REAL,
                sl_price REAL,
                size REAL NOT NULL,
                status TEXT DEFAULT 'OPEN',
                exit_price REAL,
                pnl REAL,
                close_timestamp TEXT,
                reason TEXT,
                grade TEXT
            );

            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                balance REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0
            );
        """)
        await self._conn.commit()

        # Migration: เพิ่ม column reason ถ้ายังไม่มี (สำหรับ DB เก่าที่สร้างก่อน column นี้)
        try:
            await self._conn.execute("ALTER TABLE trades ADD COLUMN reason TEXT")
            await self._conn.commit()
        except Exception:
            pass  # column มีอยู่แล้ว

        # Migration: เพิ่ม column grade ถ้ายังไม่มี (สำหรับ DB เก่าที่สร้างก่อน column นี้)
        try:
            await self._conn.execute("ALTER TABLE trades ADD COLUMN grade TEXT")
            await self._conn.commit()
        except Exception:
            pass  # column มีอยู่แล้ว

        logger.debug("Database tables created/verified")

    async def save_signal(self, signal) -> None:
        """
        บันทึก AgentSignal ลง agent_signals table
        รับ AgentSignal dataclass จาก base_agent
        """
        try:
            await self._conn.execute(
                """INSERT INTO agent_signals
                   (timestamp, agent_name, signal, score, confidence, reason, price)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal.timestamp,
                    signal.agent_name,
                    signal.signal,
                    signal.score,
                    signal.confidence,
                    signal.reason,
                    getattr(signal, "price", None),
                ),
            )
            await self._conn.commit()
        except Exception as e:
            logger.error(f"save_signal error: {e}")

    async def save_master_decision(
        self,
        timestamp: str,
        final_signal: str,
        total_score: float,
        llm_reasoning: str = "",
        was_executed: int = 0,
    ) -> None:
        """บันทึกการตัดสินใจของ Master Agent"""
        try:
            await self._conn.execute(
                """INSERT INTO master_decisions
                   (timestamp, final_signal, total_score, llm_reasoning, was_executed)
                   VALUES (?, ?, ?, ?, ?)""",
                (timestamp, final_signal, total_score, llm_reasoning, was_executed),
            )
            await self._conn.commit()
        except Exception as e:
            logger.error(f"save_master_decision error: {e}")

    async def save_trade(
        self,
        side: str,
        entry_price: float,
        size: float,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
        reason: Optional[str] = None,
        grade: Optional[str] = None,
    ) -> int:
        """บันทึก trade ใหม่ คืน trade id"""
        try:
            cursor = await self._conn.execute(
                """INSERT INTO trades
                   (timestamp, side, entry_price, tp_price, sl_price, size, status, reason, grade)
                   VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)""",
                (datetime.utcnow().isoformat(), side, entry_price, tp_price, sl_price, size, reason, grade),
            )
            await self._conn.commit()
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"save_trade error: {e}")
            return -1

    async def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        pnl: float,
        status: str = "CLOSED",
    ) -> None:
        """อัปเดต trade เมื่อปิด position"""
        try:
            await self._conn.execute(
                """UPDATE trades
                   SET exit_price=?, pnl=?, status=?, close_timestamp=?
                   WHERE id=?""",
                (exit_price, pnl, status, datetime.utcnow().isoformat(), trade_id),
            )
            await self._conn.commit()
        except Exception as e:
            logger.error(f"close_trade error: {e}")

    async def save_balance(self, balance: float, unrealized_pnl: float = 0.0) -> None:
        """บันทึก balance snapshot ทุกชั่วโมง"""
        try:
            await self._conn.execute(
                """INSERT INTO balance_history (timestamp, balance, unrealized_pnl)
                   VALUES (?, ?, ?)""",
                (datetime.utcnow().isoformat(), balance, unrealized_pnl),
            )
            await self._conn.commit()
        except Exception as e:
            logger.error(f"save_balance error: {e}")

    async def get_open_trades(self) -> list:
        """ดึง trade ที่ยัง OPEN อยู่ สำหรับ PositionMonitor ตรวจ TP/SL"""
        try:
            cursor = await self._conn.execute(
                "SELECT * FROM trades WHERE status = 'OPEN'"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"get_open_trades error: {e}")
            return []

    async def get_total_pnl(self) -> float:
        """P&L รวมทั้งหมดตั้งแต่เริ่มต้น (ไม่จำกัดวัน)"""
        try:
            cursor = await self._conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status IN ('CLOSED', 'STOPPED')"
            )
            row = await cursor.fetchone()
            return float(row[0]) if row else 0.0
        except Exception as e:
            logger.error(f"get_total_pnl error: {e}")
            return 0.0

    async def get_trade_stats(self) -> dict:
        """สถิติ trades: win rate, avg PnL, total trades"""
        try:
            cursor = await self._conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                    COALESCE(AVG(pnl), 0) as avg_pnl,
                    COALESCE(SUM(pnl), 0) as total_pnl,
                    COALESCE(MAX(pnl), 0) as best_trade,
                    COALESCE(MIN(pnl), 0) as worst_trade
                FROM trades WHERE status IN ('CLOSED', 'STOPPED')
            """)
            row = await cursor.fetchone()
            if not row or row["total"] == 0:
                return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                        "avg_pnl": 0, "total_pnl": 0, "best_trade": 0, "worst_trade": 0}
            d = dict(row)
            d["win_rate"] = round(d["wins"] / d["total"] * 100, 1) if d["total"] > 0 else 0
            return d
        except Exception as e:
            logger.error(f"get_trade_stats error: {e}")
            return {}

    async def get_today_pnl(self) -> float:
        """
        คำนวณ PnL รวมของวันนี้จาก trades ที่ปิดแล้ว
        ใช้ใน Risk Agent ตรวจสอบ daily loss limit
        """
        try:
            today = date.today().isoformat()
            cursor = await self._conn.execute(
                """SELECT COALESCE(SUM(pnl), 0) as total_pnl
                   FROM trades
                   WHERE status IN ('CLOSED', 'STOPPED')
                   AND DATE(close_timestamp) = ?""",
                (today,),
            )
            row = await cursor.fetchone()
            return float(row["total_pnl"]) if row else 0.0
        except Exception as e:
            logger.error(f"get_today_pnl error: {e}")
            return 0.0

    async def get_recent_signals(self, agent_name: str = None, limit: int = 20) -> list:
        """ดึง signal ล่าสุด ใช้แสดงใน dashboard"""
        try:
            if agent_name:
                cursor = await self._conn.execute(
                    """SELECT * FROM agent_signals WHERE agent_name=?
                       ORDER BY id DESC LIMIT ?""",
                    (agent_name, limit),
                )
            else:
                cursor = await self._conn.execute(
                    "SELECT * FROM agent_signals ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"get_recent_signals error: {e}")
            return []

    async def get_recent_trades(self, limit: int = 20) -> list:
        """ดึง trade ล่าสุด 20 รายการ ใช้แสดงใน dashboard"""
        try:
            cursor = await self._conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"get_recent_trades error: {e}")
            return []

    async def get_balance_history(self, days: int = 30) -> list:
        """ดึง balance history 30 วัน ใช้วาด PnL chart"""
        try:
            cursor = await self._conn.execute(
                """SELECT * FROM balance_history
                   ORDER BY id DESC LIMIT ?""",
                (days * 24,),  # สูงสุด 24 records ต่อวัน (บันทึกทุกชั่วโมง)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in reversed(rows)]
        except Exception as e:
            logger.error(f"get_balance_history error: {e}")
            return []

    async def get_latest_master_decision(self) -> Optional[dict]:
        """ดึงการตัดสินใจล่าสุดของ Master Agent"""
        try:
            cursor = await self._conn.execute(
                "SELECT * FROM master_decisions ORDER BY id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"get_latest_master_decision error: {e}")
            return None
