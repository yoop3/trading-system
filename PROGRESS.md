# PROGRESS.md — Claude Code จะอัปเดตไฟล์นี้เอง

## สถานะโดยรวม
- [x] Phase 1: Foundation ✅
- [x] Phase 2: Agents ✅
- [x] Phase 3: Executor ✅
- [x] Phase 4: Dashboard ✅
- [x] Phase 5: Integration & Main ✅
- [x] Phase 6: Pre-deployment (DigitalOcean ready) ✅
- [x] Phase 7: **11-Agent BTC+XAU Rebuild** ✅ (2026-06-17)
- [x] Phase 8: **13-Agent Expansion + Bug Fixes** ✅ (2026-06-17)
- [x] Phase 9: **Signal Quality Fixes** ✅ (2026-06-17)
- [x] Phase 10: **Confidence & Master Fixes** ✅ (2026-06-17)
- [x] Phase 11: **Signal Reversal Close + Unrealized PnL Dashboard** ✅ (2026-06-17)

## ทำล่าสุดถึง
**Phase 11: Signal Reversal Auto-Close + Open Position PnL**

### Phase 11: Signal Reversal Close + Unrealized PnL Dashboard (2026-06-17)

#### Signal Reversal Close Logic (4 เงื่อนไข)
**ไฟล์: `core/database.py`**
- เพิ่ม `get_recent_master_decisions_for_asset(asset_prefix, limit=3)` — query `master_decisions WHERE final_signal LIKE 'BTC:%'` ORDER BY id DESC

**ไฟล์: `core/executor.py`**
- เพิ่ม `TAKER_FEE_PCT = 0.0005` class constant
- เพิ่ม `close_paper_position(asset, reason)` — ปิด paper trades ทุก trade สำหรับ asset นั้น คำนวณ net PnL (gross - fee) บันทึกลง DB

**ไฟล์: `main.py`**
- เพิ่ม `_check_reversal_and_close()` helper ใน `TradingSystem`:
  - Condition 1: signal ตรงข้ามกับ side ของ position ปัจจุบัน
  - Condition 2: `|total_score| > reversal_score_min` (BTC: 8, XAU: 7)
  - Condition 3: `master_conf >= 0.60` (60%)
  - Condition 4: signal ติดต่อกัน >= 3 รอบ (ตรวจจาก `master_decisions` DB)
  - ถ้าผ่านทุกข้อ → ปิด position → reload positions → Risk Agent ตรวจใหม่
- `_run_btc_master()`: เรียก `_check_reversal_and_close(reversal_score_min=8.0)` ก่อน Risk check
- `_run_xau_master()`: เรียก `_check_reversal_and_close(reversal_score_min=7.0)` ก่อน Risk check

#### Unrealized PnL Dashboard
**ไฟล์: `dashboard/server.py`**
- เพิ่ม `_TAKER_FEE_PCT = 0.0005` class constant
- เพิ่ม `paper_positions: []` ใน initial state
- ใน `_refresh_state()`: query `get_open_trades()` → fetch current price → คำนวณ unrealized PnL (gross - close-side fee) → เก็บใน `state["paper_positions"]`

**ไฟล์: `dashboard/index.html`**
- เพิ่ม card "Open Paper Positions (Unrealized PnL)" ระหว่าง Overview กับ Paper Trade Stats
- `renderPaperPositions(positions)` แสดงตาราง: Trade ID, Asset, Side, Entry, Current, Size, Unrealized PnL (สีเขียว/แดง), เวลาเปิด
- เรียกใน `render(state)` ทุกครั้งที่ได้ WebSocket update

#### หลักการ
- ถ้าไม่ครบ 4 เงื่อนไข → ถือ position เดิม รอ signal ยืนยันรอบต่อไป
- ปิดแล้ว Risk Agent ยังต้องอนุมัติก่อนเปิดใหม่ (เหมือน normal flow)
- Unrealized PnL อัปเดตทุก 5 วินาทีตาม broadcast loop

---

**13-Agent BTC+XAU System** — Bug fixes + Architecture expansion

### Phase 8: 13-Agent Expansion + Bug Fixes (2026-06-17)

#### Bug Fixes
1. **Confidence 0% (Root Cause)**: ไม่ใช่ formula bug — เกิดจาก XAU agents ขาด macro/wyckoff input
   แก้ด้วยการเพิ่ม macro_xau + wyckoff_xau เข้า XAU pipeline (ดู #3 #4)
   Master confidence formula แก้: BTC=abs(score)/12.0, XAU=abs(score)/10.0 (ที่ threshold → 50%)
2. **Risk Agent IDLE**: แก้ใน `check_asset()` — HOLD path ตอนนี้ return APPROVED+monitoring
   แทน VETO, dashboard แสดง DONE/APPROVED เสมอแม้ master HOLD

#### ไฟล์ใหม่ (4)
- `agents/macro_btc_agent.py` — rename+refactor จาก `macro_agent.py`, class `MacroBTCAgent`, name `"macro_btc"`
- `agents/wyckoff_btc_agent.py` — rename+refactor จาก `wyckoff_agent.py`, class `WyckoffBTCAgent`, name `"wyckoff_btc"`
- `agents/macro_xau_agent.py` — **NEW**: XAU 4H/1D macro analysis + economic calendar check
  - Economic calendar: `https://nfs.faireconomy.media/ff_calendar_thisweek.json`
  - USD High Impact events (CPI/NFP/FOMC) ใน 24h → confidence ×0.7
  - Rules: Market Structure HH/HL ±3, EMA200 4H ±2, Weekly ±2, Monthly (10% threshold) ±2
  - Max score ±9, confidence = min(abs(score)/9, 1.0)
- `agents/wyckoff_xau_agent.py` — **NEW**: XAU 1D Wyckoff analysis
  - LOOKBACK=120 วัน (XAU cycles 2-3x ยาวกว่า BTC — ใช้ 30-day window แทน 20-day)
  - Events เดียวกัน: Spring/SOS/SC/UTAD/SOW/BC + volume trend
  - Score -3..+3, confidence = abs(score)/3.0

#### อัปเดต Master Weights
- BTC (14.0): `technical_btc×2.0, whale_btc×2.0, smc_btc×3.0, macro_btc×2.5, wyckoff_btc×2.0, sentiment×1.5, news×1.0`
  - Renamed keys: `macro`→`macro_btc`, `wyckoff`→`wyckoff_btc`
  - threshold ±6 (ไม่เปลี่ยน)
- XAU (12.5): `wyckoff_xau×2.0, macro_xau×3.0, smc_xau×3.0, technical_xau×2.0, news×2.0, sentiment×0.5`
  - threshold ±5 (เปลี่ยนจาก ±3)
  - min_weight_ratio 40% (5.0 weight ขั้นต่ำ)

#### Dashboard Groups (index.html)
- **SHARED (2)**: Sentiment, News
- **BTC (6)**: Technical BTC, Whale BTC, SMC BTC, Macro BTC, Wyckoff BTC, Master BTC
- **XAU (5)**: SMC XAU, Technical XAU, Macro XAU, Wyckoff XAU, Master XAU
- **CONTROL**: Risk

#### หมายเหตุ Architecture
- `_btc_signals["sentiment"]` / `_xau_signals["sentiment"]` ใช้ object เดียวกัน (shared agent)
- `macro_agent.py` และ `wyckoff_agent.py` ยังอยู่ แต่ไม่ได้ import ใน main.py แล้ว (สามารถลบทีหลัง)
- aiohttp ต้องมีใน requirements สำหรับ macro_xau economic calendar

#### ทดสอบแล้ว
- import test ทุก agent ผ่านหมด (python3 -c "import main" → OK)
- BTC_TOTAL_WEIGHT = 14.0 ✅, XAU_TOTAL_WEIGHT = 12.5 ✅

### Phase 10: Confidence & Master Fixes (2026-06-17)

1. **Wyckoff BTC/XAU Conf 0% fix** (`wyckoff_btc_agent.py`, `wyckoff_xau_agent.py`)
   - `confidence = max(0.10, abs(score)/3.0)` — ขั้นต่ำ 10% เมื่อ agent ทำงานสำเร็จ
   - Conf 0% เกิดได้แค่ตอน error path เท่านั้น

2. **Master XAU agent count (dashboard label)**
   - BTC section: "5 specialist" — scoring ใช้ 7 agents (5+news+sentiment)
   - XAU section: "4 specialist" — scoring ใช้ 6 agents (4+news+sentiment)
   - label สะท้อนว่า shared agents อยู่ใน SHARED section แต่ยังนับเข้า scoring ทั้งคู่

3. **Master confidence จาก weighted_score (ไม่ใช่ max sub-agent)**
   - `main.py._run_btc_master()`: `master_conf = min(abs(btc_decision.total_score)/12.0, 1.0)`
   - `main.py._run_xau_master()`: `master_conf = min(abs(xau_decision.total_score)/10.0, 1.0)`
   - สอดคล้องกับ formula ใน dashboard และ Risk Agent check
   - BTC: score=12 (2×threshold) → 100% conf | XAU: score=10 (2×threshold) → 100% conf

### Phase 9: Signal Quality Fixes (2026-06-17)

1. **Macro XAU Conf 0% fix** (`macro_xau_agent.py`)
   - เพิ่ม `confidence = max(0.10, confidence)` หลังคำนวณ
   - ผล: ถ้า agent ทำงานสำเร็จแต่ตลาด neutral → confidence ขั้นต่ำ 10% (ไม่ใช่ 0%)

2. **Master XAU Logic fix** (`master_agent.py`)
   - `XAU_MIN_WEIGHT_RATIO = 0.0` (เปลี่ยนจาก 0.40)
   - ผล: ถ้า `total_score > 5` → LONG ทันที ไม่ต้องรอว่า weight เกิน 40%
   - หลักการ: total weighted score คือ consensus แล้ว ไม่จำเป็นต้องตรวจซ้ำ

3. **SMC BTC/XAU Ranging fallback** (`smc_btc_agent.py`, `smc_xau_agent.py`)
   - BTC: ถ้า `score==0 AND htf_trend==RANGING AND active_fvgs` → score=±0.5 ตาม FVG type, confidence≥20%
   - XAU: ถ้า `score==0 AND active_fvgs` → score=±0.5 ตาม FVG type, confidence≥20%
   - ผล: Master Agent ได้ input เล็กน้อย (±0.5×weight) แม้ตลาด ranging — แทน 0.0 ตลอด

## ทำล่าสุดถึง (Phase 7)
**11-Agent BTC+XAU System** — Rebuild จาก 7-agent ETH เป็น 11-agent BTC+XAU

### 11-Agent Architecture (2026-06-17)

#### BTC Agents (7 — consensus ตัดสินใจ BTC trade)
- `agents/macro_agent.py` — **Updated**: ใช้ BTC/USDT:USDT data, เพิ่ม Rule 4 (monthly trend), fix confidence = min(abs(score)/9.0, 1.0)
- `agents/news_agent.py` — **Updated**: เปลี่ยน Reddit r/ethereum → r/Bitcoin, prompt ปรับ ETH→BTC
- `agents/sentiment_agent.py` — **Updated**: ใช้ BTC/USDT:USDT สำหรับ funding rate + OI
- `agents/wyckoff_agent.py` — **NEW**: BTC 1D + Volume, ตรวจ Wyckoff events (Spring/SOS/SC/BC/SOW/UTAD) → score -3..+3
- `agents/technical_btc_agent.py` — **NEW**: BTC 1H/15m/5m, EMA20/50, RSI14, MACD, BB, Volume → score -9..+9
- `agents/whale_btc_agent.py` — **NEW**: BTC order book 20 levels, bid/ask ratio, large trades >$500K → score -4..+4
- `agents/smc_btc_agent.py` — **NEW**: Top-down BTC SMC (4H FVG + 1D CHoCH/BOS → 5m stop hunt/displacement/OB) → score -3..+3

#### XAU Agents (2 — consensus ตัดสินใจ XAU trade)
- `agents/smc_xau_agent.py` — **NEW** (replaces old smc_agent.py wrapper): 1H FVG + round number liquidity ($50 intervals) + optional news avoidance (XAU_NEWS_AVOIDANCE=true) → score -3..+3
- `agents/technical_xau_agent.py` — **NEW**: XAU 1H/15m/5m เหมือน BTC technical + ATR volatility check (>2x avg → conf 50%) → score -9..+9

#### Control (2)
- `agents/risk_agent.py` — **Updated**: เพิ่ม `check_asset(asset_symbol, signal, conf, score, positions, is_xau)` method — per-asset position limit, XAU news VETO (is_xau=True + XAU_NEWS_AVOIDANCE=true)
- `agents/master_agent.py` — **Rebuilt**: `decide_btc(signals)` + `decide_xau(signals)` แยก threshold (BTC ±6 of 14.0, XAU ±3 of 5.0), BTC ใช้ LLM grey zone, XAU HOLD if grey zone

#### Infrastructure
- `core/data_fetcher.py` — **Updated**: เพิ่ม `symbol` param ให้ `get_order_book`, `get_recent_trades`, `get_funding_rate`, `get_open_interest`
- `main.py` — **Rebuilt**: `TradingSystem` มี `_btc_signals`/`_xau_signals` แยก, `_run_btc_master()` + `_run_xau_master()` แยก pipeline
- `dashboard/server.py` — **Updated**: เพิ่ม `update_master_btc()` + `update_master_xau()`, state มี `master_btc_decision`/`master_xau_decision`
- `dashboard/index.html` — **Updated**: title/header ปรับ, AGENT_ORDER 12 entries (incl. master_btc/master_xau), dual master decision cards, agent icons/names ทุกตัว

#### ทดสอบแล้ว
- `python3 -m pytest agents/smc_agent/tests/ -q` → 32 passed
- `python3 -m pytest tests/ -q` → 8 passed
- Import test ทุก agent ผ่านหมด (wyckoff, technical_btc, whale_btc, smc_btc, smc_xau, technical_xau, master, risk)

#### Note: Old files ยังอยู่ (ไม่ได้ลบ)
- `agents/technical_agent.py`, `agents/whale_agent.py` — ETH agents เดิม ไม่ได้ใช้ใน main.py แล้ว (สามารถลบทีหลัง)
- `agents/smc_agent/` directory — detectors ยังคงอยู่และ import โดย smc_btc_agent + smc_xau_agent
- `agents/smc_agent/smc_agent.py`, `agents/smc_agent/config.py` — old wrapper ยังอยู่ แต่ไม่ได้ import ใน main.py แล้ว

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

### SMC Agent เทรด BTCUSDT เพิ่ม (เพิ่มใหม่)
- `agents/smc_agent/config.py` — เพิ่ม `BTC_CONFIG` (spread จาก `SMC_CONFIG`, เปลี่ยน `symbol`="BTC/USDT:USDT",
  `asset`="BTCUSDT", ปรับ `min_fvg_size`=60.0 และ `sl_buffer`=450.0 ตามสเกลราคา BTC เทียบ XAU
  — **เป็นค่าประมาณเริ่มต้น ยังไม่ผ่าน backtest ควรดู paper trading จริงแล้วปรับ**)
- `agents/smc_agent/smc_agent.py` — `SMCAgent.__init__` รับ `name` param (default "smc") เพื่อแยก log/signal
  ของแต่ละ instance (`smc_xau`/`smc_btc`); `last_smc_output` เพิ่ม `min_score_to_signal`/`min_tp2_rr`
  จาก config ของตัวเอง
- `agents/risk_agent.py` — `check_smc()` อ่าน threshold (`min_score_to_signal`/`min_tp2_rr`) จาก
  `smc_output` แทนการ import `SMC_CONFIG` ตรงๆ ทำให้ generalize ใช้ได้กับทุก asset/config
- `main.py` — สร้าง `self.smc_xau` (SMC_CONFIG) และ `self.smc_btc` (BTC_CONFIG) แยกกัน,
  `_run_smc_if_due`/`_handle_smc_signal` เปลี่ยนเป็น generic รับ `(key, agent, cfg)` ใช้ร่วมกันทั้งสอง asset
- `dashboard/index.html` — เพิ่ม agent card "₿ SMC (BTCUSDT)" (key `smc_btc`)
- หมายเหตุ: weekday-only killzone gate (`session.py`) ใช้ sessions/lookback เดียวกันกับ XAU ทั้งคู่
  — BTC เทรด 24/7 จริง แต่ยังให้ตรวจ killzone แบบเดียวกับ XAU (Mon-Fri, London/NY) เพื่อความง่าย
  ถ้าพบว่า BTC มี setup ดีๆ ช่วงเสาร์-อาทิตย์บ่อย ค่อยพิจารณาแยก session config
- ตรวจสอบแล้ว: BTC_CONFIG/SMCAgent(name=...)/risk_agent.check_smc() generalized ทำงานถูกต้อง,
  unit tests เดิม (8) + SMC detector tests (32) ผ่านหมด

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
