"""
displacement.py — STEP 5: Displacement Detection (5m)
หา displacement candle (momentum candle ใหญ่กว่า avgBody*2) ที่เกิดหลัง stop hunt + เช็ค MSB
"""

import pandas as pd


def detect_displacement(
    df: pd.DataFrame,
    stop_hunt: dict,
    max_bars_after: int = 3,
    avg_body_bars: int = 10,
) -> dict:
    """
    avgBody = mean(abs(close - open), avg_body_bars bars ก่อนแท่งที่ตรวจ)

    Bullish Displacement (เกิดหลัง Bullish Stop Hunt <= max_bars_after bars):
        close > open, (close - open) >= avgBody * 2.0
        close > highest high ใน avg_body_bars bars ก่อนหน้า (MSB)

    Bearish Displacement (เกิดหลัง Bearish Stop Hunt <= max_bars_after bars):
        close < open, (open - close) >= avgBody * 2.0
        close < lowest low ใน avg_body_bars bars ก่อนหน้า (MSB)

    คืน {detected, type, msb, bar_index} — bar_index ใช้โดย detect_order_block
    ในการหาแท่ง OB ก่อน displacement candle นี้
    """
    if not stop_hunt.get("detected"):
        return {"detected": False, "type": None, "msb": False, "bar_index": -1}

    hunt_idx = stop_hunt["bar_index"]
    stop_type = stop_hunt["type"]
    n = len(df)
    body = (df["close"] - df["open"]).abs()

    for idx in range(hunt_idx + 1, min(hunt_idx + max_bars_after + 1, n)):
        window = df.iloc[max(0, idx - avg_body_bars): idx]
        if window.empty:
            continue
        avg_body = body.iloc[max(0, idx - avg_body_bars): idx].mean()
        if pd.isna(avg_body) or avg_body <= 0:
            continue

        bar = df.iloc[idx]

        if stop_type == "BULL":
            if bar["close"] > bar["open"] and (bar["close"] - bar["open"]) >= avg_body * 2.0:
                msb = bool(bar["close"] > window["high"].max())
                return {"detected": True, "type": "BULL", "msb": msb, "bar_index": idx}

        else:  # BEAR
            if bar["close"] < bar["open"] and (bar["open"] - bar["close"]) >= avg_body * 2.0:
                msb = bool(bar["close"] < window["low"].min())
                return {"detected": True, "type": "BEAR", "msb": msb, "bar_index": idx}

    return {"detected": False, "type": None, "msb": False, "bar_index": -1}
