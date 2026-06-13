"""
fvg.py — STEP 1: HTF FVG (Fair Value Gap) Detection
สแกน 3-candle pattern บน HTF (1H) หา Bullish/Bearish FVG แล้วเช็ค invalidation
"""

import pandas as pd


def detect_htf_fvg(
    df: pd.DataFrame,
    min_gap: float = 2.0,
    max_fill_pct: float = 75.0,
) -> list[dict]:
    """
    Bullish FVG: candles[i-2].high < candles[i].low
        gap = candles[i].low - candles[i-2].high >= min_gap
        fvg.top = candles[i].low, fvg.bottom = candles[i-2].high

    Bearish FVG: candles[i-2].low > candles[i].high
        gap = candles[i-2].low - candles[i].high >= min_gap
        fvg.top = candles[i-2].low, fvg.bottom = candles[i].high

    Invalidation: ดู fill% จากแท่งหลัง FVG ก่อตัว ถ้า fill% > max_fill_pct -> active = False

    คืน list ของ {type, top, bottom, active, fill_pct}
    """
    fvgs: list[dict] = []
    n = len(df)

    for i in range(2, n):
        c_i = df.iloc[i]
        c_im2 = df.iloc[i - 2]

        # Bullish FVG — gap ระหว่าง high แท่ง i-2 กับ low แท่ง i
        if c_im2["high"] < c_i["low"]:
            gap = c_i["low"] - c_im2["high"]
            if gap >= min_gap:
                fvgs.append(
                    _build_fvg("BULL", float(c_i["low"]), float(c_im2["high"]), df, i, max_fill_pct)
                )

        # Bearish FVG — gap ระหว่าง low แท่ง i-2 กับ high แท่ง i
        if c_im2["low"] > c_i["high"]:
            gap = c_im2["low"] - c_i["high"]
            if gap >= min_gap:
                fvgs.append(
                    _build_fvg("BEAR", float(c_im2["low"]), float(c_i["high"]), df, i, max_fill_pct)
                )

    return fvgs


def price_in_active_fvg(price: float, fvgs: list[dict], fvg_type: str) -> bool:
    """True ถ้า price อยู่ใน [bottom, top] ของ active FVG ชนิด fvg_type ใดก็ได้"""
    for fvg in fvgs:
        if fvg.get("type") == fvg_type and fvg.get("active"):
            if fvg["bottom"] <= price <= fvg["top"]:
                return True
    return False


def _build_fvg(
    fvg_type: str,
    top: float,
    bottom: float,
    df: pd.DataFrame,
    formed_idx: int,
    max_fill_pct: float,
) -> dict:
    """คำนวณ fill% จากแท่งหลังจาก FVG ก่อตัว แล้วเช็ค invalidation"""
    after = df.iloc[formed_idx + 1:]
    fvg_range = top - bottom

    if after.empty or fvg_range <= 0:
        fill_pct = 0.0
    elif fvg_type == "BULL":
        # ราคาย่อกลับลงมาเติม gap จากด้านบน -> ใช้ lowest low หลังก่อตัว
        extreme = float(after["low"].min())
        fill_pct = (top - extreme) / fvg_range * 100
    else:  # BEAR
        # ราคาเด้งกลับขึ้นมาเติม gap จากด้านล่าง -> ใช้ highest high หลังก่อตัว
        extreme = float(after["high"].max())
        fill_pct = (extreme - bottom) / fvg_range * 100

    fill_pct = max(0.0, min(100.0, fill_pct))

    return {
        "type": fvg_type,
        "top": round(top, 2),
        "bottom": round(bottom, 2),
        "active": fill_pct <= max_fill_pct,
        "fill_pct": round(fill_pct, 2),
    }
