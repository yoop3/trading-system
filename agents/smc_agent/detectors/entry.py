"""
entry.py — STEP 7: Entry Signal
ตรวจว่าราคา retrace กลับมาแตะ Order Block และ OB อยู่ใน FVG zone หรือไม่
"""

import pandas as pd

from agents.smc_agent.detectors.fvg import price_in_active_fvg


def check_entry(df: pd.DataFrame, ob: dict | None, fvg_active: list[dict]) -> dict:
    """
    Long Entry — ราคา retrace กลับมาแตะ Bullish OB:
        current.low <= ob.top, current.close > ob.bottom
        ob.top อยู่ใน Bullish FVG zone

    Short Entry — ราคา retrace ขึ้นมาแตะ Bearish OB:
        current.high >= ob.bottom, current.close < ob.top
        ob.bottom อยู่ใน Bearish FVG zone

    คืน {signal: "LONG"|"SHORT"|"NO_SETUP"}
    """
    if ob is None or df.empty:
        return {"signal": "NO_SETUP"}

    current = df.iloc[-1]

    if ob["type"] == "BULL":
        if current["low"] <= ob["top"] and current["close"] > ob["bottom"]:
            if price_in_active_fvg(ob["top"], fvg_active, "BULL"):
                return {"signal": "LONG"}

    elif ob["type"] == "BEAR":
        if current["high"] >= ob["bottom"] and current["close"] < ob["top"]:
            if price_in_active_fvg(ob["bottom"], fvg_active, "BEAR"):
                return {"signal": "SHORT"}

    return {"signal": "NO_SETUP"}
