# SMC Agent Spec — Plug into Existing Multi-Agent System

## Context
เพิ่ม SMC Agent เข้าไปใน Multi-Agent Trading System ที่มีอยู่แล้ว
Flow ใหม่: SMC Agent → Risk Agent → Master Agent
ไม่แตะ Agent เดิม (Technical, Macro, Sentiment, News, Whale) เลย

---

## Position ใน System

```
[Existing Agents]          [New Flow]
Technical Agent ──┐
Macro Agent ──────┤
Sentiment Agent ──┼──→ Master Agent
News Agent ────── ┤         ↑
Whale Agent ──────┘         │
                      Risk Agent
                            ↑
                       SMC Agent  ← เพิ่มตรงนี้
```

SMC Agent รันแยกอิสระ ส่งผลไป Risk Agent ก่อน
Risk Agent ตัดสินใจ VETO หรือส่งต่อ Master Agent

---

## Data Requirements

### HTF Data (1H candles)
```
Symbol: XAUSUSDT (เพิ่มจาก ETH ที่มีอยู่)
Interval: 1h
Lookback: 50 bars
Fields: open, high, low, close, timestamp
```

### LTF Data (5m candles)
```
Symbol: XAUSUSDT
Interval: 5m
Lookback: 30 bars
Fields: open, high, low, close, timestamp
```

ดึงจาก Binance API เดิมที่ระบบใช้อยู่แล้ว เพียงแค่เพิ่ม Symbol และ Interval

---

## SMC Agent Logic

### STEP 1 — HTF FVG Detection

```python
def detect_htf_fvg(candles_1h):
    """
    Bullish FVG:
    - candles[i-2].high < candles[i].low
    - gap = candles[i].low - candles[i-2].high >= 2.0 points
    - fvg.top = candles[i].low
    - fvg.bottom = candles[i-2].high
    
    Bearish FVG:
    - candles[i-2].low > candles[i].high
    - gap = candles[i-2].low - candles[i].high >= 2.0 points
    - fvg.top = candles[i-2].low
    - fvg.bottom = candles[i].high
    
    Invalidation:
    - fill% = (fvg.top - current_low) / (fvg.top - fvg.bottom) * 100
    - if fill% > 75 → active = False
    
    Return: list of active FVGs [{type, top, bottom, active}]
    """
```

### STEP 2 — HTF Liquidity Detection

```python
def detect_htf_liquidity(candles_1h, lookback=20):
    """
    BSL (Buy Side Liquidity):
    - highest high ใน lookback bars
    - Equal Highs: high[i] และ high[j] ต่างกัน <= 0.1% → BSL
    
    SSL (Sell Side Liquidity):
    - lowest low ใน lookback bars
    - Equal Lows: low[i] และ low[j] ต่างกัน <= 0.1% → SSL
    
    Return: {
        bsl: float,  # nearest BSL above current price
        ssl: float,  # nearest SSL below current price
        bsl2: float, # second BSL
        ssl2: float  # second SSL
    }
    """
```

### STEP 3 — Session Filter

```python
def in_killzone(timestamp_utc):
    """
    London: 07:00 - 10:00 UTC
    New York: 12:00 - 15:00 UTC
    
    Return: bool
    """
    hour = timestamp_utc.hour
    return (7 <= hour < 10) or (12 <= hour < 15)
```

### STEP 4 — Stop Hunt Detection (5m)

```python
def detect_stop_hunt(candles_5m, active_fvgs, lookback=10):
    """
    swingLow  = min(low, lookback bars)
    swingHigh = max(high, lookback bars)
    
    Bullish Stop Hunt:
    - current.low < swingLow (เจาะลง)
    - current.close > swingLow (ปิดกลับขึ้น)
    - current.close > current.open (candle เขียว)
    - current.low อยู่ใน Bullish FVG zone
    
    Bearish Stop Hunt:
    - current.high > swingHigh (เจาะขึ้น)
    - current.close < swingHigh (ปิดกลับลง)
    - current.close < current.open (candle แดง)
    - current.high อยู่ใน Bearish FVG zone
    
    Return: {detected: bool, type: "BULL"|"BEAR"|None, bar_index: int}
    """
```

### STEP 5 — Displacement Detection (5m)

```python
def detect_displacement(candles_5m, stop_hunt, max_bars_after=3):
    """
    avgBody = mean(abs(close - open), 10 bars)
    
    Bullish Displacement (เกิดหลัง Bullish Stop Hunt ≤ 3 bars):
    - close > open
    - (close - open) >= avgBody * 2.0
    - close > highest high ใน 10 bars (MSB)
    
    Bearish Displacement (เกิดหลัง Bearish Stop Hunt ≤ 3 bars):
    - close < open
    - (open - close) >= avgBody * 2.0
    - close < lowest low ใน 10 bars (MSB)
    
    Return: {detected: bool, type: "BULL"|"BEAR"|None, msb: bool}
    """
```

### STEP 6 — Order Block Detection (5m)

```python
def detect_order_block(candles_5m, displacement):
    """
    Bullish OB = แท่ง Bearish (close < open) สุดท้าย
                 ก่อนเกิด Bullish Displacement
    - ob.top    = open
    - ob.bottom = close
    
    Bearish OB = แท่ง Bullish (close > open) สุดท้าย
                 ก่อนเกิด Bearish Displacement
    - ob.top    = close
    - ob.bottom = open
    
    Return: {top: float, bottom: float, type: "BULL"|"BEAR"}
    """
```

### STEP 7 — Entry Signal

```python
def check_entry(candles_5m, ob, fvg_active):
    """
    Long Entry — ราคา retrace กลับมาแตะ OB:
    - current.low <= ob.top
    - current.close > ob.bottom
    - ob.top อยู่ใน Bullish FVG zone
    
    Short Entry — ราคา retrace ขึ้นมาแตะ OB:
    - current.high >= ob.bottom
    - current.close < ob.top
    - ob.bottom อยู่ใน Bearish FVG zone
    
    Return: {signal: "LONG"|"SHORT"|"NO_SETUP"}
    """
```

---

## Scoring System

```python
def calculate_score(criteria):
    """
    criteria = {
        htf_fvg:      bool,  # +1
        session:      bool,  # +1
        stop_hunt:    bool,  # +1
        displacement: bool,  # +1 (+ MSB bonus)
        ob_detected:  bool,  # +1
        rr_ok:        bool,  # +1 (RR >= 1.5)
    }
    
    score = sum of criteria met
    max_score = 6
    
    Map to -3 to +3:
    - 6/6 met → +3
    - 5/6 met → +2
    - 4/6 met → +1
    - < 4 met → NO_SETUP (score = 0)
    
    confidence = (criteria_met / 6) * 100
    """
```

---

## SMC Agent Output

```json
{
  "agent": "SMC",
  "timestamp": "2024-01-15T14:30:00Z",
  "asset": "XAUSUSDT",
  "signal": "LONG | SHORT | NO_SETUP",
  "score": -3,
  "confidence": 83,
  "criteria": {
    "htf_fvg_active": true,
    "in_session": true,
    "stop_hunt": true,
    "displacement": true,
    "ob_detected": true,
    "rr_sufficient": true
  },
  "levels": {
    "entry": 4224.50,
    "sl": 4210.00,
    "tp1": 4245.00,
    "tp2": 4280.00,
    "rr_tp1": 1.5,
    "rr_tp2": 3.8
  },
  "context": {
    "fvg_fill_pct": 32.5,
    "session": "NEW_YORK",
    "htf_trend": "BULLISH",
    "liquidity_above": 4285.00,
    "liquidity_below": 4190.00
  },
  "reason": "Bullish FVG 1H active (32% fill) | Stop Hunt ใน NY session | Displacement + MSB | OB confirmed"
}
```

---

## Risk Agent Integration

Risk Agent รับ SMC Output แล้วเช็ค:

```python
def smc_risk_check(smc_output, current_positions):
    """
    VETO conditions:
    - smc_output.signal == "NO_SETUP" → VETO
    - smc_output.score < 2 → VETO
    - smc_output.levels.rr_tp2 < 1.5 → VETO
    - max positions reached → VETO
    - asset already has open position → VETO
    
    PASS conditions:
    - ครบทุกข้อข้างบน
    - ส่ง levels (entry, sl, tp1, tp2) ให้ Master Agent
    """
```

---

## Master Agent Integration

Master Agent รับ SMC Vote เพิ่มเป็น 1 ใน N agents:

```python
smc_vote = {
    "agent": "SMC",
    "direction": "LONG | SHORT | HOLD",
    "score": int,        # -3 to +3
    "confidence": float, # 0-100
    "asset": "XAUSUSDT", # แยก asset จาก ETH เดิม
    "levels": {...}      # ส่ง SL/TP ไปด้วย
}
```

**หมายเหตุ**: SMC Agent เทรด XAUUSDT แยกจาก ETH เดิม
Master Agent ควร handle multi-asset ถ้ายังไม่รองรับ

---

## Run Frequency

```
ทุก 5 นาที (sync กับ LTF candle close)
- ดึง 1H data ล่าสุด 50 bars
- ดึง 5m data ล่าสุด 30 bars
- รัน detection pipeline ทั้งหมด
- ส่ง output ไป Risk Agent
```

---

## File Structure (แนะนำ)

```
agents/
├── smc_agent/
│   ├── smc_agent.py         ← Main agent file
│   ├── detectors/
│   │   ├── fvg.py           ← FVG detection
│   │   ├── liquidity.py     ← Liquidity levels
│   │   ├── stop_hunt.py     ← Stop hunt detection
│   │   ├── displacement.py  ← Displacement + MSB
│   │   └── order_block.py   ← OB detection
│   ├── config.py            ← Parameters (ปรับได้)
│   └── tests/
│       └── test_detectors.py
```

---

## Config Parameters (ปรับได้)

```python
SMC_CONFIG = {
    "symbol": "XAUSUSDT",
    "htf": "1h",
    "ltf": "5m",
    "htf_lookback": 50,
    "ltf_lookback": 30,
    
    # FVG
    "min_fvg_size": 2.0,        # points
    "max_fvg_fill_pct": 75.0,   # %
    
    # OB
    "ob_lookback": 10,           # bars
    
    # Risk
    "sl_buffer": 15.0,           # points
    "tp1_rr": 1.5,
    "min_tp2_rr": 1.5,
    
    # Session (UTC)
    "sessions": {
        "london": (7, 10),
        "new_york": (12, 15)
    },
    
    # Scoring
    "min_score_to_signal": 2     # ต้องได้อย่างน้อย 4/6 criteria
}
```

---

## Notes สำหรับ Claude Code

1. ดู pattern ของ Agent เดิมในระบบ แล้วทำ SMC Agent ให้ match รูปแบบเดิม
2. ใช้ Binance API client เดิมที่มีอยู่แล้ว อย่าสร้างใหม่
3. SMC Agent ต้อง stateless — ไม่เก็บ state ข้าม run
4. Log ทุก detection step เพื่อ debug ง่าย
5. Unit test detector แต่ละตัวแยกกัน
6. ถ้า Master Agent ยังไม่รองรับ multi-asset ให้แจ้งและ suggest วิธี extend
