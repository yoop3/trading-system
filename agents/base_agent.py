"""
base_agent.py — Base class ที่ทุก agent inherit
กำหนด interface มาตรฐาน: analyze(), run(), และ AgentSignal dataclass
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger


@dataclass
class AgentSignal:
    """
    โครงสร้างสัญญาณที่ทุก agent คืนกลับมา
    ใช้ทั่วทั้งระบบ ตั้งแต่ agent ถึง master ถึง database
    """
    agent_name: str        # ชื่อ agent เช่น "technical"
    signal: str            # "LONG", "SHORT", "HOLD"
    score: float           # -10 ถึง +10
    confidence: float      # 0.0 ถึง 1.0
    reason: str            # เหตุผลสั้นๆ
    timestamp: str         # ISO format
    next_action: str = ""  # สิ่งที่ agent จะทำ/รอในรอบถัดไป
    price: Optional[float] = None  # ราคา ETH ตอนนั้น
    veto: bool = False     # True = Risk Agent บล็อก trade นี้
    recommended_size: Optional[float] = None    # สำหรับ Risk Agent
    recommended_leverage: Optional[int] = None  # สำหรับ Risk Agent


class BaseAgent(ABC):
    """
    Base class ที่ agent ทั้ง 7 ตัว inherit
    บังคับให้ implement analyze() และจัดการ lifecycle (status, logging, db save)
    """

    def __init__(self, name: str, data_fetcher, db):
        self.name = name
        self.data_fetcher = data_fetcher
        self.db = db
        self.last_signal: Optional[AgentSignal] = None
        # status: IDLE → ANALYZING → DONE (หรือ ERROR)
        self.status: str = "IDLE"

    @abstractmethod
    async def analyze(self) -> AgentSignal:
        """แต่ละ agent implement logic เอง — คืน AgentSignal เสมอ"""
        pass

    async def run(self) -> AgentSignal:
        """
        เรียก analyze() → บันทึกลง DB → อัปเดต status
        ทุก agent ใช้ method นี้แทนการเรียก analyze() โดยตรง
        """
        self.status = "ANALYZING"
        try:
            signal = await self.analyze()
            self.last_signal = signal
            self.status = "DONE"
            # บันทึกลง database (ยกเว้น Risk Agent ที่ไม่มี signal ปกติ)
            if signal.signal not in ("APPROVED", "VETO"):
                await self.db.save_signal(signal)
            logger.info(
                f"[{self.name}] {signal.signal} score={signal.score:+.1f} "
                f"conf={signal.confidence:.0%} | {signal.reason}"
            )
            return signal
        except Exception as e:
            self.status = "ERROR"
            logger.error(f"[{self.name}] run() failed: {e}")
            # คืน HOLD signal เพื่อให้ระบบทำงานต่อได้แม้ agent นี้ error
            from datetime import datetime, timezone
            fallback = AgentSignal(
                agent_name=self.name,
                signal="HOLD",
                score=0.0,
                confidence=0.0,
                reason=f"Error: {str(e)[:100]}",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            self.last_signal = fallback
            return fallback
