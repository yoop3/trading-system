"""
config.py — SMC_CONFIG: พารามิเตอร์ทั้งหมดของ SMC Agent (ปรับได้)
อ้างอิงจาก SMC_Agent_Spec.md
"""

SMC_CONFIG = {
    # symbol ใช้ ccxt unified format เดียวกับ TRADING_SYMBOL ("ETH/USDT:USDT")
    # ตรวจสอบแล้วว่า Binance Futures Testnet มี XAUUSDT (gold) -> "XAU/USDT:USDT"
    "symbol": "XAU/USDT:USDT",
    "asset": "XAUUSDT",  # ชื่อแสดงผล (binance symbol style) สำหรับ output/asset
    "htf": "1h",
    "ltf": "5m",
    "htf_lookback": 50,
    "ltf_lookback": 30,

    # FVG (Fair Value Gap)
    "min_fvg_size": 2.0,         # points (ขั้นต่ำของ gap)
    "max_fvg_fill_pct": 75.0,    # % ถ้า fill เกินนี้ ถือว่า FVG ไม่ active แล้ว

    # Liquidity
    "liquidity_lookback": 20,    # bars ย้อนหลังสำหรับหา BSL/SSL
    "liquidity_eq_threshold_pct": 0.1,  # % ความต่างที่ถือว่าเป็น "equal highs/lows"

    # Stop Hunt
    "stop_hunt_lookback": 10,    # bars สำหรับหา swing high/low

    # Displacement
    "displacement_avg_body_bars": 10,   # bars สำหรับคำนวณ avgBody
    "displacement_max_bars_after": 3,   # ต้องเกิดภายในกี่ bar หลัง stop hunt

    # Order Block
    "ob_lookback": 10,            # bars

    # Risk / Levels
    "sl_buffer": 15.0,             # points
    "tp1_rr": 1.5,
    "min_tp2_rr": 1.5,

    # Session (UTC) — killzones
    "sessions": {
        "london": (7, 10),
        "new_york": (12, 15),
    },

    # Scoring — ใช้โดย Risk Agent: |score| ต้อง >= ค่านี้ จึงผ่าน
    "min_score_to_signal": 2,
}

# BTC_CONFIG — เหมือน SMC_CONFIG แต่เปลี่ยน symbol/asset เป็น BTCUSDT
# min_fvg_size/sl_buffer ปรับสเกลตามราคา BTC (~30 เท่าของ XAU) -> เป็นค่าประมาณเริ่มต้น
# ควรดู paper trading จริงแล้วปรับ (กลยุทธ์เดียวกัน เกณฑ์อื่นๆ เป็น % หรือจำนวน bar ไม่ต้องปรับ)
BTC_CONFIG = {
    **SMC_CONFIG,
    "symbol": "BTC/USDT:USDT",
    "asset": "BTCUSDT",
    "min_fvg_size": 60.0,   # points (ของเดิม XAU = 2.0)
    "sl_buffer": 450.0,     # points (ของเดิม XAU = 15.0)
}
