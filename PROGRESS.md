# PROGRESS.md — Claude Code จะอัปเดตไฟล์นี้เอง

## สถานะโดยรวม
- [x] Phase 1: Foundation ✅
- [x] Phase 2: Agents ✅
- [x] Phase 3: Executor ✅
- [x] Phase 4: Dashboard ✅
- [x] Phase 5: Integration & Main ✅
- [x] Phase 6: Pre-deployment (DigitalOcean ready) ✅

## ทำล่าสุดถึง
**DEPLOYMENT READY** — ระบบพร้อม paper trade 2 สัปดาห์ และ deploy บน DigitalOcean

### Trade Grading (เพิ่มใหม่)
- `docs/TRADE_GRADING.md` — เกณฑ์ให้เกรด A/B/C/D (consensus weight + signal strength + confidence + volatility modifier)
- `core/trade_grader.py` — `grade_trade()` คำนวณเกรดอัตโนมัติตอนเปิด trade ไม่เรียก LLM เพิ่ม
- `trades.grade` column ใหม่ใน DB (มี migration สำหรับ DB เก่า) + แสดงใน dashboard (คอลัมน์ Grade)
- `agents/master_agent.py`: `MasterDecision` เพิ่ม field `weight_ratio` (ใช้คำนวณเกรด)

### Trading Fee Simulation (เพิ่มใหม่)
- `core/position_monitor.py` — หัก taker fee จำลอง 0.05% ต่อฝั่ง (entry+exit) ออกจาก PnL ทุกครั้งที่ปิด trade
  ทำให้สถิติ paper trading ใกล้เคียงของจริงมากขึ้น (trade ที่ PnL ใกล้ 0 อาจกลายเป็นขาดทุนสุทธิ)
- แก้ label log PnL จาก "ETH" → "USDT" (หน่วยที่ถูกต้อง) ไปในตัว

### SMC Agent (เพิ่มใหม่ — ตาม SMC_Agent_Spec.md)
- `agents/smc_agent/` — Smart Money Concepts agent วิเคราะห์ XAUUSDT (symbol="XAU/USDT:USDT")
  - `config.py` — `SMC_CONFIG` พารามิเตอร์ทั้งหมด (FVG, liquidity, session, stop hunt, displacement, OB, risk levels)
  - `detectors/fvg.py` — STEP 1: HTF FVG (Fair Value Gap) + invalidation (fill%) + `price_in_active_fvg()` helper
  - `detectors/liquidity.py` — STEP 2: BSL/SSL จาก swing high/low + equal highs/lows
  - `detectors/session.py` — STEP 3: Killzone filter (London 07-10 UTC, New York 12-15 UTC)
  - `detectors/stop_hunt.py` — STEP 4: Liquidity sweep detection (5m)
  - `detectors/displacement.py` — STEP 5: Momentum candle + MSB (market structure break)
  - `detectors/order_block.py` — STEP 6: Order Block จากแท่งก่อน displacement
  - `detectors/entry.py` — STEP 7: Entry signal (retrace เข้า OB + อยู่ใน active FVG zone)
  - `detectors/scoring.py` — แปลง criteria 6 ข้อ → score -3..+3 + confidence
  - `smc_agent.py` — `SMCAgent(BaseAgent)` รัน pipeline ทั้งหมด, เก็บผลลัพธ์เต็มไว้ที่ `self.last_smc_output`
  - `tests/test_detectors.py` — unit tests ทุก detector (30 tests, ผ่านหมด)
- `agents/risk_agent.py` — เพิ่ม `check_smc(smc_output, current_positions)`: VETO ถ้า NO_SETUP /
  |score| < `min_score_to_signal` / RR tp2 ไม่พอ / asset มี position เปิดอยู่แล้ว, ไม่งั้น APPROVED พร้อม levels + size/leverage
- `core/data_fetcher.py` — `get_current_price()` / `get_ohlcv()` รับ `symbol` parameter (optional, default `self.symbol`)
  เพื่อให้ SMC Agent ดึงข้อมูล XAUUSDT แยกจาก symbol หลัก (ETHUSDT) ได้

### Multi-Asset Paper Trading — SMC (XAUUSDT) รันคู่กับ ETHUSDT (เพิ่มใหม่)
- `core/database.py`:
  - เพิ่ม column `trades.asset` (default `'ETH/USDT:USDT'`) + migration สำหรับ DB เก่า
  - `save_trade(..., asset=None)` — ถ้าไม่ระบุ ใช้ `TRADING_SYMBOL` env (ETHUSDT)
  - `get_open_trades(asset=None)` — filter ตาม asset ได้ (ใช้ตรวจ position limit/PnL แยกราย asset)
- `core/executor.py` — `open_long`/`open_short`/`_open_position` รับ `asset` (ccxt symbol) เพื่อเปิด/บันทึก trade
  ของ asset ใดก็ได้ (ไม่ระบุ = ใช้ `self.symbol`/ETHUSDT ตามเดิม)
- `core/position_monitor.py` — `check()` group open trades ตาม asset แล้วดึงราคาแยกแต่ละ asset
  (ของเก่าไม่มี `asset` → fallback เป็น symbol หลักของ DataFetcher)
- `agents/risk_agent.py` — `_check_risk()` (ETHUSDT consensus) เช็ค `MAX_OPEN_POSITIONS` โดย filter
  `get_open_trades(asset=ETHUSDT)` เท่านั้น เพื่อไม่ให้ XAUUSDT trade ไปนับรวมโควต้าของ ETHUSDT
- `main.py`:
  - สร้าง `SMCAgent` ใน `TradingSystem.__init__`, เพิ่ม `"smc": 5*60` ใน `_intervals`
  - `_run_smc_if_due()` — รัน SMC ตาม schedule ของตัวเอง (ไม่เก็บเข้า `self._signals`/ไม่ผ่าน Master Agent
    เพราะเป็นคนละ asset)
  - `_handle_smc_signal()` — ส่งผล SMC ผ่าน `risk.check_smc()` แล้ว execute ด้วย `executor.open_long/open_short(
    ..., asset=SMC_CONFIG["symbol"])` โดยใช้ `levels["tp1"]`/`levels["sl"]` เป็น TP/SL ของ trade
  - SMC trades ไม่มี `grade`/`grade_detail` (เกณฑ์ใน `trade_grader.py` ออกแบบมาสำหรับ ETHUSDT weighted-consensus
    เท่านั้น ไม่ apply กับ SMC scoring model)
- `dashboard/index.html` — เพิ่ม agent card "SMC (XAUUSDT)" + คอลัมน์ "Asset" ในตาราง trade history
  (`assetLabel()` แปลง ccxt symbol เช่น `"XAU/USDT:USDT"` → `"XAUUSDT"` สำหรับแสดงผล)
- ตรวจสอบแล้ว: migration บน DB เก่า (ไม่มี column asset) ทำงานถูกต้อง, `save_trade`/`get_open_trades`
  ทำงานถูกต้องทั้งกรณีระบุ/ไม่ระบุ asset, unit tests เดิม (8) + SMC detector tests (30) ผ่านหมด

### ค้างไว้ทำต่อ (Phase ถัดไป)
- เก็บข้อมูล closed trades ให้ได้ ~15-20 ไม้ก่อน แล้วดู win rate แยกตามเกรด (A/B/C/D)
- ถ้าเกรด A/B win rate ดีกว่าชัดเจน → feed สรุป win-rate-by-grade กลับเข้า prompt ของ master_agent (LLM) เป็น "memory"
- พิจารณา filter ให้เทรดเฉพาะเกรด A (หรือ A/B) เมื่อข้อมูลพอ
- **`Executor._current_trade_id`** เป็น instance variable เดียว ใช้แค่ใน `close_position()` (real-trading only)
  ถ้าจะเปิด real trading ทั้ง ETHUSDT และ XAUUSDT พร้อมกันต้องแก้ให้ track แยกตาม asset ก่อน

## ไฟล์ที่สร้างแล้ว

### Phase 1 — Foundation
- `requirements.txt` — **pinned exact versions** สำหรับ reproducible builds
- `.env.example`
- `.env`
- `core/__init__.py`
- `core/data_fetcher.py`
- `core/indicators.py`
- `core/database.py` — เพิ่ม `get_open_trades()`, `get_total_pnl()`, `get_trade_stats()`

### Phase 2 — Agents
- `agents/__init__.py`
- `agents/base_agent.py`
- `agents/technical_agent.py`
- `agents/macro_agent.py`
- `agents/sentiment_agent.py`
- `agents/news_agent.py` — **RSS version** (CoinTelegraph + CoinDesk + Reddit r/ethereum)
- `agents/whale_agent.py`
- `agents/risk_agent.py`
- `agents/master_agent.py` — **Weighted consensus** (≥40% of total weight 9.5)

### Phase 3 — Executor
- `core/executor.py` — TRADING_ENABLED=false → SIMULATED mode

### Phase 4 — Dashboard
- `dashboard/__init__.py`
- `dashboard/server.py` — เพิ่ม `/health` + `/api/stats` endpoints
- `dashboard/index.html`

### Phase 5 — Integration
- `main.py` — รัน PositionMonitor ทุก 30 วินาที

### Phase 6 — Deployment
- `core/position_monitor.py` — ตรวจ TP/SL paper trades ทุก loop
- `Dockerfile` — python:3.11-slim สำหรับ Docker
- `docker-compose.yml` — restart: unless-stopped + healthcheck + volume mounts
- `.dockerignore`
- `trading-bot.service` — systemd service สำหรับ DigitalOcean (non-Docker)

### Tests
- `tests/__init__.py`
- `tests/test_agents.py`
- `tests/test_data_fetcher.py`
- `agents/smc_agent/tests/test_detectors.py` — unit tests สำหรับ SMC detectors (30 tests)

## ปัญหาที่เจอและแก้แล้ว

1. **ccxt==4.3.0 ไม่มีใน PyPI** → แก้เป็น `ccxt>=4.3.1` (ตอนนี้ pin เป็น 4.5.56)
2. **pandas-ta ไม่รองรับ Python 3.14** → ลบออก เขียน indicators ด้วย pandas ล้วนๆ
3. **Binance sandbox mode deprecated** → ใช้ mainnet keys, override URL ไม่จำเป็น
4. **fetch_balance() เรียก sapi endpoint** → เปลี่ยนเป็น `fapiPrivateV2GetBalance()` ตรงๆ
5. **OpenInterest parse ผิด field** → แก้เป็น `openInterestAmount`
6. **CryptoPanic ต้องการ API key** → เปลี่ยนเป็น RSS feeds (ฟรี ไม่ต้อง key)
7. **Consensus เข้มงวดเกินไป** (3/5 agent) → เปลี่ยนเป็น weighted consensus ≥40% of 9.5

## RSS Sources (News Agent)
| แหล่ง | URL |
|-------|-----|
| CoinTelegraph | https://cointelegraph.com/rss |
| CoinDesk | https://www.coindesk.com/arc/outboundfeeds/rss/ |
| Reddit r/ethereum | https://reddit.com/r/ethereum/.rss |

## Weighted Consensus (Master Agent)
| Agent | Weight |
|-------|--------|
| macro | 3.0 |
| technical | 2.0 |
| whale | 2.0 |
| sentiment | 1.5 |
| news | 1.0 |
| **TOTAL** | **9.5** |

เงื่อนไขเทรด: score >+5 หรือ <-5 **และ** dominant side ≥40% (3.8/9.5 weight)

## วิธีรัน

### Local (background)
```bash
nohup python3 main.py > logs/main.log 2>&1 &
# เปิด http://localhost:8000
tail -f logs/main.log
```

### Docker
```bash
docker-compose up -d
docker-compose logs -f
```

### DigitalOcean (systemd)
```bash
sudo cp trading-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable trading-bot
sudo systemctl start trading-bot
journalctl -u trading-bot -f
```

## DigitalOcean Spec แนะนำ
- **Basic Droplet** $12/month: 2GB RAM, 1 vCPU, 50GB SSD
- Ubuntu 22.04 LTS
- ติดตั้ง: Python 3.11, pip, git (หรือใช้ Docker)

## รันทดสอบ
```bash
python3 -m pytest tests/ -v
```

## Health Check
```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/stats
```
