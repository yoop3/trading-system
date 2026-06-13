"""
session.py — STEP 3: Session Filter (Killzones)
London 07:00-10:00 UTC, New York 12:00-15:00 UTC (อ่านจาก SMC_CONFIG["sessions"])
"""

from datetime import datetime

from agents.smc_agent.config import SMC_CONFIG


def in_killzone(timestamp_utc: datetime, sessions: dict | None = None) -> bool:
    """True ถ้าเวลาอยู่ใน killzone ใดๆ (London หรือ New York) ตาม UTC"""
    sessions = sessions or SMC_CONFIG["sessions"]
    hour = timestamp_utc.hour
    return any(start <= hour < end for start, end in sessions.values())


def get_session_name(timestamp_utc: datetime, sessions: dict | None = None) -> str:
    """คืนชื่อ session ปัจจุบัน: LONDON / NEW_YORK / OUTSIDE"""
    sessions = sessions or SMC_CONFIG["sessions"]
    hour = timestamp_utc.hour
    for name, (start, end) in sessions.items():
        if start <= hour < end:
            return name.upper()
    return "OUTSIDE"
