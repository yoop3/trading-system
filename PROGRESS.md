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

### ค้างไว้ทำต่อ (Phase ถัดไป)
- เก็บข้อมูล closed trades ให้ได้ ~15-20 ไม้ก่อน แล้วดู win rate แยกตามเกรด (A/B/C/D)
- ถ้าเกรด A/B win rate ดีกว่าชัดเจน → feed สรุป win-rate-by-grade กลับเข้า prompt ของ master_agent (LLM) เป็น "memory"
- พิจารณา filter ให้เทรดเฉพาะเกรด A (หรือ A/B) เมื่อข้อมูลพอ
- (cosmetic, ยังไม่ทำ) `core/position_monitor.py` log PnL หน่วย "ETH" ควรเป็น "USDT"

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
