# CLAUDE.md — AI Trading System (7 Agents)

## อ่านไฟล์นี้ทุกครั้งก่อนทำงาน

คุณกำลังช่วยสร้างระบบ AI Trading สำหรับ ETHUSDT Futures บน Binance Testnet
โดยใช้สถาปัตยกรรม 7 Specialized Agents ตัดสินใจร่วมกัน

---

## กฎเหล็กที่ห้ามละเมิด

1. **ใช้ Testnet เท่านั้น** — URL: `https://testnet.binancefuture.com`
   ห้ามใช้ mainnet URL `https://fapi.binance.com` เด็ดขาด
2. **ห้าม hardcode API key** — ใช้ `.env` เสมอ ผ่าน `python-dotenv`
3. **ก่อนรัน order จริงทุกครั้ง** — ต้องผ่าน Risk Agent ก่อนเสมอ
4. **ทุก error ต้อง log** — อย่า silent fail เด็ดขาด
5. **Comment ภาษาไทย+อังกฤษ** — เจ้าของโปรเจคอ่านทั้งสองภาษา

---

## วิธีทำงาน

- ทำทีละ module ให้เสร็จและ test ก่อนไปต่อ
- หลังสร้างแต่ละไฟล์ให้รัน `python -c "import <module>"` เพื่อตรวจ syntax
- ถ้า error ให้แก้เองก่อน ไม่ต้องถามถ้าแก้ได้
- อัปเดต `PROGRESS.md` ทุกครั้งที่ module เสร็จ
- ถ้า session หลุดให้อ่าน `PROGRESS.md` แล้วทำต่อได้เลย

---

## โครงสร้างโปรเจค

```
trading-system/
├── CLAUDE.md          ← ไฟล์นี้ (Claude อ่านอัตโนมัติ)
├── SPEC.md            ← รายละเอียดระบบทั้งหมด
├── PROGRESS.md        ← Claude เขียนเองว่าทำถึงไหน
├── .env               ← API keys (ห้าม commit)
├── .env.example       ← template ให้ user ดู
├── requirements.txt
├── main.py            ← จุดเริ่มต้น `python main.py`
│
├── agents/
│   ├── __init__.py
│   ├── base_agent.py      ← base class ที่ทุกตัว inherit
│   ├── news_agent.py
│   ├── macro_agent.py
│   ├── technical_agent.py
│   ├── sentiment_agent.py
│   ├── whale_agent.py
│   ├── risk_agent.py
│   └── master_agent.py
│
├── core/
│   ├── __init__.py
│   ├── data_fetcher.py    ← ดึงราคา OHLCV จาก Binance
│   ├── indicators.py      ← คำนวณ RSI, MACD, EMA ฯลฯ
│   ├── executor.py        ← ส่ง order ไป Binance Testnet
│   └── database.py        ← SQLite บันทึกทุก trade และ signal
│
├── dashboard/
│   ├── server.py          ← FastAPI + WebSocket
│   └── index.html         ← หน้าเว็บ real-time dashboard
│
└── tests/
    ├── test_agents.py
    └── test_data_fetcher.py
```

---

## Stack ที่ใช้

- Python 3.11+
- ccxt (Binance Futures Testnet)
- anthropic (Claude API สำหรับ LLM reasoning)
- pandas, pandas-ta (indicators — ใช้ pandas-ta แทน ta-lib เพราะติดตั้งง่ายกว่า)
- FastAPI + uvicorn (dashboard backend)
- websockets (real-time update)
- SQLite + aiosqlite (database)
- python-dotenv (จัดการ .env)
- schedule (timer สำหรับแต่ละ agent)
- loguru (logging สวยๆ)

---

## Style Guide

```python
# ตัวอย่าง comment ที่ดี
async def fetch_price(symbol: str) -> float:
    """
    ดึงราคาปัจจุบันของ symbol จาก Binance Testnet
    คืนค่าเป็น float เช่น 2136.50
    """
    # ใช้ ccxt เพราะรองรับ testnet และ unified API
    ticker = await exchange.fetch_ticker(symbol)
    return ticker['last']  # last = ราคาล่าสุด
```

- ใช้ type hints ทุกฟังก์ชัน
- ใช้ async/await สำหรับ I/O ทั้งหมด
- ใช้ dataclass หรือ TypedDict สำหรับ signal structure

---

## Signal Structure (ใช้ทั่วทั้งระบบ)

```python
from dataclasses import dataclass

@dataclass
class AgentSignal:
    agent_name: str        # ชื่อ agent เช่น "technical"
    signal: str            # "LONG", "SHORT", "HOLD"
    score: float           # -10 ถึง +10
    confidence: float      # 0.0 ถึง 1.0
    reason: str            # เหตุผลสั้นๆ
    timestamp: str         # ISO format
```
