"""
stop_hunt.py — STEP 4: Stop Hunt Detection (5m)
ตรวจจับ liquidity sweep: เจาะ swing high/low แล้วปิดกลับเข้าแนวเดิม + อยู่ใน FVG zone
"""

import pandas as pd

from agents.smc_agent.detectors.fvg import price_in_active_fvg


def detect_stop_hunt(
    df: pd.DataFrame,
    active_fvgs: list[dict],
    lookback: int = 10,
    scan_bars: int = 4,
) -> dict:
    """
    swingLow/swingHigh คำนวณจาก `lookback` แท่งก่อนแท่งที่ตรวจ

    Bullish Stop Hunt:
        bar.low < swingLow, bar.close > swingLow, bar.close > bar.open (เขียว)
        bar.low อยู่ใน Bullish FVG zone (active)

    Bearish Stop Hunt:
        bar.high > swingHigh, bar.close < swingHigh, bar.close < bar.open (แดง)
        bar.high อยู่ใน Bearish FVG zone (active)

    สแกน `scan_bars` แท่งล่าสุด (ใหม่ -> เก่า) คืนรายการที่เจอก่อน เพราะ
    detect_displacement ต้องใช้ bar_index นี้หา displacement ภายใน N bars ถัดไป

    คืน {detected, type, bar_index} — bar_index = absolute position (.iloc) ใน df
    """
    n = len(df)
    start = max(lookback, n - scan_bars)

    for idx in range(n - 1, start - 1, -1):
        window = df.iloc[idx - lookback: idx]
        if window.empty:
            continue

        bar = df.iloc[idx]
        swing_low = window["low"].min()
        swing_high = window["high"].max()

        # Bullish Stop Hunt — เจาะ swing low แล้วปิดกลับขึ้น
        if bar["low"] < swing_low and bar["close"] > swing_low and bar["close"] > bar["open"]:
            if price_in_active_fvg(bar["low"], active_fvgs, "BULL"):
                return {"detected": True, "type": "BULL", "bar_index": idx}

        # Bearish Stop Hunt — เจาะ swing high แล้วปิดกลับลง
        if bar["high"] > swing_high and bar["close"] < swing_high and bar["close"] < bar["open"]:
            if price_in_active_fvg(bar["high"], active_fvgs, "BEAR"):
                return {"detected": True, "type": "BEAR", "bar_index": idx}

    return {"detected": False, "type": None, "bar_index": -1}
