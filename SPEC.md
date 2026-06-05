# SPEC.md — รายละเอียดระบบ AI Trading ทั้งหมด

## เป้าหมาย

สร้างระบบ AI Trading ETHUSDT Perpetual Futures บน Binance Testnet
ใช้ 7 Specialized Agents วิเคราะห์คนละมุม แล้วให้ Master Agent ตัดสินใจขั้นสุดท้าย
มี Dashboard แสดงผล real-time ทุก agent พร้อม status, history, balance, PnL

---

## ขั้นตอนการสร้างตามลำดับ

### Phase 1 — Foundation (ทำก่อน)
1. สร้าง `requirements.txt` และ `.env.example`
2. สร้าง `core/data_fetcher.py` — ดึงราคาจาก Binance Testnet
3. สร้าง `core/indicators.py` — คำนวณ indicator ทั้งหมด
4. สร้าง `core/database.py` — สร้าง SQLite tables
5. ทดสอบดึงราคาได้จริงก่อนไปทำ agent

### Phase 2 — Agents (ทำตามลำดับนี้)
1. `agents/base_agent.py` — base class
2. `agents/technical_agent.py` — ทำก่อนเพราะสำคัญสุด
3. `agents/macro_agent.py`
4. `agents/sentiment_agent.py`
5. `agents/news_agent.py`
6. `agents/whale_agent.py`
7. `agents/risk_agent.py`
8. `agents/master_agent.py` — ทำสุดท้าย รับ output จากทุกตัว

### Phase 3 — Executor
1. `core/executor.py` — ส่ง order ไป Binance Testnet

### Phase 4 — Dashboard
1. `dashboard/server.py` — FastAPI + WebSocket
2. `dashboard/index.html` — UI แสดงผล real-time

### Phase 5 — Main & Integration
1. `main.py` — orchestrate ทุกอย่างให้ทำงานพร้อมกัน
2. ทดสอบ end-to-end

---

## Phase 1 รายละเอียด

### requirements.txt
```
ccxt==4.3.0
anthropic==0.25.0
pandas==2.2.0
pandas-ta==0.3.14b
fastapi==0.111.0
uvicorn==0.29.0
websockets==12.0
aiosqlite==0.20.0
python-dotenv==1.0.0
schedule==1.2.1
loguru==0.7.2
httpx==0.27.0
```

### .env.example
```
# Binance Testnet API
BINANCE_API_KEY=your_testnet_api_key_here
BINANCE_API_SECRET=your_testnet_api_secret_here
BINANCE_TESTNET=true

# Anthropic API (สำหรับ Master Agent LLM)
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# CryptoPanic API (สำหรับ News Agent)
CRYPTOPANIC_API_KEY=your_cryptopanic_key_here

# ตั้งค่าระบบ
TRADING_SYMBOL=ETH/USDT:USDT
INITIAL_BALANCE=10000
LOG_LEVEL=INFO
DASHBOARD_PORT=8000
```

### core/data_fetcher.py ต้องทำได้
```python
# สิ่งที่ต้อง implement:

class DataFetcher:
    # connect ไป Binance Testnet ผ่าน ccxt
    # testnet URL: https://testnet.binancefuture.com
    
    async def get_current_price(self) -> float:
        # ดึงราคา ETH/USDT ปัจจุบัน
    
    async def get_ohlcv(self, timeframe: str, limit: int) -> pd.DataFrame:
        # timeframe: '5m', '15m', '1h', '4h', '1d'
        # คืน DataFrame มี columns: timestamp, open, high, low, close, volume
    
    async def get_balance(self) -> dict:
        # คืน {'total': 10000.0, 'free': 8500.0, 'used': 1500.0}
    
    async def get_open_positions(self) -> list:
        # คืน list ของ position ที่เปิดอยู่
    
    async def get_funding_rate(self) -> float:
        # ดึง funding rate ปัจจุบัน (สำหรับ Sentiment Agent)
    
    async def get_open_interest(self) -> float:
        # ดึง open interest รวม (สำหรับ Sentiment Agent)
```

### core/indicators.py ต้องคำนวณได้
```python
# ใช้ pandas-ta library

class Indicators:
    def calculate_all(self, df: pd.DataFrame) -> pd.DataFrame:
        # เพิ่ม columns ต่อไปนี้ลงใน df แล้วคืนกลับ:
        
        # Trend
        # - EMA_20, EMA_50, EMA_200
        # - MACD (macd, signal, histogram)
        
        # Momentum  
        # - RSI_14 (0-100)
        # - Stochastic (stoch_k, stoch_d)
        
        # Volatility
        # - ATR_14 (Average True Range)
        # - BB_upper, BB_middle, BB_lower (Bollinger Bands)
        
        # Volume
        # - Volume_SMA_20
        # - Volume_ratio (volume ปัจจุบัน / average)
```

### core/database.py — SQLite Tables
```sql
-- สร้าง 4 tables นี้:

-- 1. บันทึก signal ของแต่ละ agent ทุกรอบ
CREATE TABLE agent_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent_name TEXT NOT NULL,      -- 'technical', 'macro', ฯลฯ
    signal TEXT NOT NULL,          -- 'LONG', 'SHORT', 'HOLD'
    score REAL NOT NULL,           -- -10 ถึง +10
    confidence REAL NOT NULL,      -- 0.0 ถึง 1.0
    reason TEXT,                   -- เหตุผล
    price REAL                     -- ราคา ETH ตอนนั้น
);

-- 2. บันทึกการตัดสินใจของ Master Agent
CREATE TABLE master_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    final_signal TEXT NOT NULL,    -- 'LONG', 'SHORT', 'HOLD'
    total_score REAL NOT NULL,
    llm_reasoning TEXT,            -- คำอธิบายจาก Claude
    was_executed INTEGER DEFAULT 0 -- 1 ถ้า order ถูกส่งจริง
);

-- 3. บันทึก trade ที่เกิดขึ้นจริง
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    side TEXT NOT NULL,            -- 'LONG' หรือ 'SHORT'
    entry_price REAL NOT NULL,
    tp_price REAL,                 -- Take Profit
    sl_price REAL,                 -- Stop Loss
    size REAL NOT NULL,            -- จำนวน ETH
    status TEXT DEFAULT 'OPEN',    -- 'OPEN', 'CLOSED', 'STOPPED'
    exit_price REAL,
    pnl REAL,
    close_timestamp TEXT
);

-- 4. บันทึก balance ทุกชั่วโมง สำหรับ 30-day PnL chart
CREATE TABLE balance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    balance REAL NOT NULL,
    unrealized_pnl REAL DEFAULT 0
);
```

---

## Phase 2 รายละเอียด Agent

### agents/base_agent.py
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class AgentSignal:
    agent_name: str
    signal: str        # 'LONG', 'SHORT', 'HOLD'
    score: float       # -10 ถึง +10
    confidence: float  # 0.0 ถึง 1.0
    reason: str
    timestamp: str
    next_action: str   # สิ่งที่ agent จะทำรอบหน้า เช่น "รอ RSI ลงมา 40"

class BaseAgent(ABC):
    def __init__(self, name: str, data_fetcher, db):
        self.name = name
        self.data_fetcher = data_fetcher
        self.db = db
        self.last_signal: AgentSignal = None
        self.status: str = "IDLE"  # IDLE, ANALYZING, DONE, ERROR
    
    @abstractmethod
    async def analyze(self) -> AgentSignal:
        # แต่ละ agent implement เอง
        pass
    
    async def run(self) -> AgentSignal:
        # เรียก analyze() แล้วบันทึกลง DB
        self.status = "ANALYZING"
        signal = await self.analyze()
        self.last_signal = signal
        self.status = "DONE"
        await self.db.save_signal(signal)
        return signal
```

---

### agents/technical_agent.py
**อัปเดตทุก 5 นาที — ใช้ rule-based ไม่ใช้ LLM (ประหยัด token)**

```
Input: กราฟ 1H, 15m, 5m + indicators ทั้งหมด
Output: AgentSignal

Logic:
1. ดู EMA trend (5m):
   - ราคา > EMA20 > EMA50 → bullish +2
   - ราคา < EMA20 < EMA50 → bearish -2

2. RSI (14) บน 1H:
   - RSI < 30 → oversold → +3
   - RSI > 70 → overbought → -3
   - RSI 40-60 → neutral 0

3. MACD (5m):
   - macd > signal และ histogram เพิ่มขึ้น → +2
   - macd < signal และ histogram ลดลง → -2

4. Volume:
   - volume_ratio > 2.0 (สูงกว่า average 2x) → เพิ่ม |score| อีก +1
   
5. Bollinger Bands (1H):
   - ราคาแตะ lower band → +1
   - ราคาแตะ upper band → -1

รวม score แล้วคำนวณ confidence = |score| / 9
next_action: บอกว่ากำลังรอสัญญาณอะไร
```

---

### agents/macro_agent.py
**อัปเดตทุก 4 ชั่วโมง — ใช้ rule-based**

```
Input: กราฟ 4H, 1D
Output: AgentSignal

Logic:
1. Market Structure (1D):
   - Higher High + Higher Low (HH/HL) → uptrend → +3
   - Lower High + Lower Low (LH/LL) → downtrend → -3

2. EMA 200 (4H):
   - ราคาอยู่เหนือ EMA200 → bullish bias +2
   - ราคาอยู่ใต้ EMA200 → bearish bias -2

3. Weekly trend:
   - เทียบราคาปัจจุบันกับ 7 วันที่แล้ว
   - +7% ขึ้นไป → strong uptrend +2
   - -7% ลงไป → strong downtrend -2
```

---

### agents/sentiment_agent.py
**อัปเดตทุก 15 นาที — ใช้ rule-based**

```
Input: Fear & Greed Index, Funding Rate, Open Interest
Output: AgentSignal

Fear & Greed Index: ดึงจาก https://api.alternative.me/fng/
- 0-25 (Extreme Fear) → contrarian LONG +3
- 25-45 (Fear) → +1
- 45-55 (Neutral) → 0
- 55-75 (Greed) → -1
- 75-100 (Extreme Greed) → contrarian SHORT -3

Funding Rate:
- > +0.1% (longs จ่าย shorts เยอะ) → market overbought → -2
- < -0.1% (shorts จ่าย longs เยอะ) → market oversold → +2
- ระหว่างกัน → 0

Open Interest:
- เพิ่มขึ้น + ราคาขึ้น → trend confirm +1
- เพิ่มขึ้น + ราคาลง → short squeeze warning -1
```

---

### agents/news_agent.py
**อัปเดตทุก 30 นาที — ใช้ LLM วิเคราะห์ข่าว**

```
Input: ข่าว 10 ชิ้นล่าสุดจาก CryptoPanic API
Output: AgentSignal

ขั้นตอน:
1. ดึงข่าว ETH/Crypto ล่าสุด 10 ชิ้นจาก:
   https://cryptopanic.com/api/v1/posts/?auth_token=KEY&currencies=ETH&kind=news

2. ส่ง headline ทั้งหมดให้ Claude วิเคราะห์ด้วย prompt:
   "วิเคราะห์ข่าวเหล่านี้ว่า sentiment ต่อ ETH เป็นบวกหรือลบ
    ตอบเป็น JSON: {score: -3 ถึง +3, reason: string, confidence: 0-1}"

3. ใช้ claude-haiku (ถูกกว่า) เพราะงานนี้ไม่ต้องการ reasoning ลึก

ถ้าไม่มี CryptoPanic key ให้ใช้ score=0 (neutral) ไปก่อน
```

---

### agents/whale_agent.py
**อัปเดตทุก 15 นาที — ใช้ rule-based**

```
Input: Large order detection จาก order book + เปรียบเทียบ volume
Output: AgentSignal

ดึงข้อมูลจาก Binance:
1. Order Book depth 20 levels
   - bid_volume_total vs ask_volume_total
   - ถ้า bid >> ask (ratio > 1.5) → buying pressure +2
   - ถ้า ask >> bid (ratio > 1.5) → selling pressure -2

2. Recent large trades (trades > $100,000)
   - ดึง recent trades แล้วกรองเฉพาะ size ใหญ่
   - ถ้า whale ซื้อเยอะ → +2
   - ถ้า whale ขายเยอะ → -2

3. ถ้าข้อมูลไม่ชัดเจน → score = 0 (HOLD)
```

---

### agents/risk_agent.py
**รันทุกครั้งก่อน execute — มีสิทธิ์ VETO**

```python
class RiskAgent(BaseAgent):
    # ค่า config ที่ใช้
    MAX_DAILY_LOSS_PCT = 0.05      # หยุดถ้าขาดทุน 5% ในวันนั้น
    MIN_CONFIDENCE = 0.60          # confidence ต่ำกว่า 60% → HOLD
    MAX_LEVERAGE = 5               # leverage สูงสุด
    RISK_PER_TRADE_PCT = 0.02      # เสี่ยงต่อ trade ไม่เกิน 2% ของ balance
    MAX_OPEN_POSITIONS = 1         # มี position เปิดพร้อมกันได้แค่ 1
    MIN_ATR_RATIO = 0.003          # volatility ต้องสูงพอ จึงเทรด

    async def analyze(self) -> AgentSignal:
        # ตรวจสอบทุกเงื่อนไข ถ้าผิดข้อไหนก็ VETO ทันที
        
        # 1. เช็ค daily loss
        daily_pnl = await self.db.get_today_pnl()
        balance = await self.data_fetcher.get_balance()
        if daily_pnl / balance['total'] < -self.MAX_DAILY_LOSS_PCT:
            return AgentSignal(veto=True, reason="Daily loss limit reached")
        
        # 2. เช็ค open positions
        positions = await self.data_fetcher.get_open_positions()
        if len(positions) >= self.MAX_OPEN_POSITIONS:
            return AgentSignal(veto=True, reason="Max positions reached")
        
        # 3. คำนวณ position size ที่ปลอดภัย
        # size = (balance * RISK_PCT) / (entry - sl)
        
        # คืน AgentSignal พร้อม recommended_size และ leverage
        return AgentSignal(
            signal="APPROVED",
            recommended_size=calculated_size,
            recommended_leverage=3,
            ...
        )
```

---

### agents/master_agent.py
**รันหลังจากได้ signal จากทุกตัว — ใช้ LLM เฉพาะตอน unclear**

```python
WEIGHTS = {
    'macro':     3.0,
    'technical': 2.0,
    'whale':     2.0,
    'sentiment': 1.5,
    'news':      1.0,
}

LONG_THRESHOLD  = +5.0   # weighted score > 5 → LONG
SHORT_THRESHOLD = -5.0   # weighted score < -5 → SHORT
# ระหว่างกัน → HOLD (ไม่เรียก LLM)

async def decide(self, signals: dict) -> MasterDecision:
    # คำนวณ weighted score
    total_score = sum(
        signals[name].score * WEIGHTS[name]
        for name in WEIGHTS
        if name in signals
    )
    
    # ถ้าชัดเจน → ตัดสินใจเองเลย ไม่เรียก LLM
    if total_score > LONG_THRESHOLD:
        return MasterDecision(signal='LONG', reasoning="Score ชัดเจน")
    if total_score < SHORT_THRESHOLD:
        return MasterDecision(signal='SHORT', reasoning="Score ชัดเจน")
    
    # ถ้า unclear (ระหว่าง -5 ถึง +5) → เรียก LLM ช่วยตัดสิน
    reasoning = await self.ask_llm(signals, total_score)
    return MasterDecision(signal='HOLD', reasoning=reasoning)

async def ask_llm(self, signals, score):
    # ส่ง summary ของทุก signal ให้ Claude วิเคราะห์
    # prompt สั้นๆ ไม่เกิน 1,500 tokens
    # ใช้ claude-sonnet-4-20250514
    # คำสั่ง: ตอบเป็น JSON เท่านั้น
    pass
```

---

## Phase 3 — Executor

### core/executor.py
```python
class Executor:
    async def open_long(self, size: float, leverage: int, tp: float, sl: float):
        # 1. ตั้ง leverage
        # 2. ส่ง market order BUY
        # 3. ตั้ง TP order (take profit)
        # 4. ตั้ง SL order (stop loss)
        # 5. บันทึกลง trades table
    
    async def open_short(self, size: float, leverage: int, tp: float, sl: float):
        # เหมือนกันแต่ฝั่ง SELL
    
    async def close_position(self):
        # ปิด position ปัจจุบัน
    
    def calculate_tp_sl(self, side: str, entry: float, atr: float):
        # TP = entry ± (ATR × 2)
        # SL = entry ∓ (ATR × 1)
        # คืน (tp_price, sl_price)
```

---

## Phase 4 — Dashboard

### สิ่งที่ต้องแสดงบน dashboard

**Section 1: ภาพรวมระบบ**
- Balance ปัจจุบัน (USDT)
- Today PnL (+/- USDT และ %)
- 30-Day PnL chart (เส้นกราฟ)
- Position ที่เปิดอยู่ (ถ้ามี): side, entry, size, unrealized PnL

**Section 2: Agent Status Cards (7 cards)**
แต่ละ card แสดง:
- ชื่อ Agent และไอคอน
- Status: 🔄 ANALYZING / ✅ DONE / ⏸ IDLE / ❌ ERROR
- Signal ล่าสุด: LONG 🟢 / SHORT 🔴 / HOLD ⚪
- Score: -10 ถึง +10 (แสดงเป็น progress bar)
- Confidence: % (แสดงเป็น progress bar)
- Reason: ข้อความสั้นๆ
- **Next Action**: สิ่งที่ agent จะทำ/รอในรอบถัดไป
- Last updated: กี่นาทีที่แล้ว

**Section 3: Master Decision**
- Total Weighted Score: ตัวเลขใหญ่
- Final Signal: LONG / SHORT / HOLD (ใหญ่ชัดเจน)
- LLM Reasoning: ถ้ามี
- Countdown ถึงรอบถัดไป

**Section 4: Trade History**
- ตาราง 20 trades ล่าสุด
- columns: เวลา, side, entry, exit, PnL, status

**Tech: ใช้ WebSocket push update ทุก 5 วินาที**
- ไม่ต้อง refresh หน้า
- ใช้ vanilla HTML/CSS/JS ก็ได้ ไม่ต้องใช้ React
- ใช้ Chart.js สำหรับ 30-day PnL chart

---

## Phase 5 — main.py

```python
# main.py orchestrate ทุกอย่าง

async def main():
    # 1. โหลด .env
    # 2. เชื่อม Binance Testnet
    # 3. สร้าง SQLite database
    # 4. สร้าง agent ทุกตัว
    # 5. เริ่ม schedule:
    #    - technical_agent: ทุก 5 นาที
    #    - sentiment_agent: ทุก 15 นาที
    #    - whale_agent: ทุก 15 นาที
    #    - news_agent: ทุก 30 นาที
    #    - macro_agent: ทุก 4 ชั่วโมง
    #    - master_agent: รันหลัง technical_agent เสมอ
    # 6. เริ่ม FastAPI dashboard ที่ port 8000
    # 7. บันทึก balance ทุกชั่วโมง

if __name__ == "__main__":
    print("🚀 AI Trading System Starting...")
    print("📊 Dashboard: http://localhost:8000")
    print("⚠️  TESTNET MODE — ไม่ใช้เงินจริง")
    asyncio.run(main())
```

---

## การทดสอบ

```bash
# ทดสอบ data fetcher
python -c "
import asyncio
from core.data_fetcher import DataFetcher
async def test():
    df = DataFetcher()
    price = await df.get_current_price()
    print(f'ETH price: {price}')
asyncio.run(test())
"

# รันระบบจริง
python main.py

# เปิด dashboard
# ไปที่ http://localhost:8000
```

---

## หมายเหตุสำคัญ

- ระบบนี้เป็น **experimental** ใช้เพื่อเรียนรู้เท่านั้น
- **Testnet ไม่มีเงินจริง** — ทดสอบได้อย่างปลอดภัย
- ก่อน go live จริงต้อง backtest อย่างน้อย 3 เดือน
- ผลตอบแทนไม่ได้รับประกัน
