"""
order_block.py — STEP 6: Order Block Detection (5m)
หาแท่งสุดท้ายที่มีสีตรงข้ามกับ displacement ก่อนเกิด displacement candle
"""

import pandas as pd

from typing import Optional


def detect_order_block(df: pd.DataFrame, displacement: dict) -> Optional[dict]:
    """
    Bullish OB = แท่ง Bearish (close < open) แท่งสุดท้าย ก่อนเกิด Bullish Displacement
        ob.top = open, ob.bottom = close

    Bearish OB = แท่ง Bullish (close > open) แท่งสุดท้าย ก่อนเกิด Bearish Displacement
        ob.top = close, ob.bottom = open

    คืน {top, bottom, type} หรือ None ถ้าไม่มี displacement หรือไม่พบแท่ง OB
    """
    if not displacement.get("detected"):
        return None

    disp_idx = displacement["bar_index"]
    disp_type = displacement["type"]

    for i in range(disp_idx - 1, -1, -1):
        bar = df.iloc[i]
        if disp_type == "BULL" and bar["close"] < bar["open"]:
            return {"top": float(bar["open"]), "bottom": float(bar["close"]), "type": "BULL"}
        if disp_type == "BEAR" and bar["close"] > bar["open"]:
            return {"top": float(bar["close"]), "bottom": float(bar["open"]), "type": "BEAR"}

    return None
