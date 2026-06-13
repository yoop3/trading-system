"""
liquidity.py — STEP 2: HTF Liquidity Detection
หา BSL (Buy Side Liquidity) และ SSL (Sell Side Liquidity) จาก swing high/low และ equal highs/lows
"""

import pandas as pd


def detect_htf_liquidity(
    df: pd.DataFrame,
    current_price: float,
    lookback: int = 20,
    eq_threshold_pct: float = 0.1,
) -> dict:
    """
    BSL = highest high ใน lookback bars + equal highs (ต่างกัน <= eq_threshold_pct%)
    SSL = lowest low ใน lookback bars + equal lows (ต่างกัน <= eq_threshold_pct%)

    คืน {bsl, ssl, bsl2, ssl2} = level ที่ใกล้ current_price ที่สุด (bsl/ssl)
    และรองลงมา (bsl2/ssl2) — None ถ้าไม่มี level ฝั่งนั้น
    """
    recent = df.tail(lookback)
    highs = recent["high"].tolist()
    lows = recent["low"].tolist()

    bsl_levels = _liquidity_levels(highs, eq_threshold_pct, extreme=max)
    ssl_levels = _liquidity_levels(lows, eq_threshold_pct, extreme=min)

    above = sorted(lvl for lvl in bsl_levels if lvl > current_price)
    below = sorted((lvl for lvl in ssl_levels if lvl < current_price), reverse=True)

    return {
        "bsl": round(above[0], 2) if len(above) > 0 else None,
        "bsl2": round(above[1], 2) if len(above) > 1 else None,
        "ssl": round(below[0], 2) if len(below) > 0 else None,
        "ssl2": round(below[1], 2) if len(below) > 1 else None,
    }


def _liquidity_levels(values: list[float], eq_threshold_pct: float, extreme) -> set[float]:
    """
    คืน set ของ level ที่ถือเป็น liquidity:
    - extreme(values) เสมอ (max สำหรับ highs/BSL, min สำหรับ lows/SSL)
    - คู่ใดๆ ที่ต่างกัน <= eq_threshold_pct% (equal highs/lows)
    """
    if not values:
        return set()

    levels = {float(extreme(values))}

    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            a, b = float(values[i]), float(values[j])
            ref = max(abs(a), abs(b))
            if ref == 0:
                continue
            if abs(a - b) / ref * 100 <= eq_threshold_pct:
                levels.add(a)
                levels.add(b)

    return levels
