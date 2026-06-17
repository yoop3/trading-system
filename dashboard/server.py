"""
server.py — FastAPI + WebSocket dashboard backend
ส่งข้อมูล real-time ทุก 5 วินาที ไปยัง frontend ผ่าน WebSocket
"""

import asyncio
import os
import sys
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from loguru import logger


class DashboardServer:
    """
    FastAPI server ที่รับ state จาก trading system
    และ push update ไปยัง browser ผ่าน WebSocket
    """

    def __init__(self):
        self.app = FastAPI(title="AI Trading Dashboard")
        self._connections: list[WebSocket] = []
        self._state: dict[str, Any] = {
            "agents": {},
            "master_btc_decision": None,
            "master_xau_decision": None,
            "master_decision": None,  # legacy compat
            "balance": {"total": 0, "free": 0, "used": 0},
            "position": None,
            "trades": [],
            "balance_history": [],
            "today_pnl": 0.0,
            "trade_stats": {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                            "avg_pnl": 0, "total_pnl": 0, "best_trade": 0, "worst_trade": 0},
            "paper_positions": [],  # open paper trades พร้อม unrealized PnL
            "last_update": "",
        }
        self._db = None
        self._data_fetcher = None
        self._setup_routes()

    def set_dependencies(self, db, data_fetcher):
        """เชื่อม database และ data fetcher จาก main.py"""
        self._db = db
        self._data_fetcher = data_fetcher

    def update_agent(self, name: str, signal) -> None:
        """อัปเดต agent state (เรียกจาก main.py หลัง agent.run())"""
        self._state["agents"][name] = {
            "name": name,
            "signal": signal.signal,
            "score": signal.score,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "next_action": signal.next_action,
            "status": "DONE",
            "timestamp": signal.timestamp,
        }

    def update_master(self, decision) -> None:
        """legacy method — เรียก update_master_btc"""
        self.update_master_btc(decision)

    def update_master_btc(self, decision) -> None:
        """อัปเดต BTC Master decision"""
        self._state["master_btc_decision"] = {
            "signal": decision.signal,
            "total_score": decision.total_score,
            "reasoning": decision.reasoning,
            "used_llm": decision.used_llm,
            "timestamp": decision.timestamp,
        }
        # confidence: score=6 (threshold) → 50%, score=12 → 100%
        self._state["agents"]["master_btc"] = {
            "name": "master_btc",
            "signal": decision.signal,
            "score": round(decision.total_score, 2),
            "confidence": min(abs(decision.total_score) / 12.0, 1.0),
            "reason": decision.reasoning,
            "next_action": "Claude LLM ช่วยตัดสิน" if decision.used_llm else "Rule-based BTC scoring",
            "status": "DONE",
            "timestamp": decision.timestamp,
        }

    def update_master_xau(self, decision) -> None:
        """อัปเดต XAU Master decision"""
        self._state["master_xau_decision"] = {
            "signal": decision.signal,
            "total_score": decision.total_score,
            "reasoning": decision.reasoning,
            "used_llm": decision.used_llm,
            "timestamp": decision.timestamp,
        }
        # confidence: score=5 (threshold) → 50%, score=10 → 100%
        self._state["agents"]["master_xau"] = {
            "name": "master_xau",
            "signal": decision.signal,
            "score": round(decision.total_score, 2),
            "confidence": min(abs(decision.total_score) / 10.0, 1.0),
            "reason": decision.reasoning,
            "next_action": "Rule-based XAU scoring (6 agents)",
            "status": "DONE",
            "timestamp": decision.timestamp,
        }

    def update_agent_status(self, name: str, status: str) -> None:
        """อัปเดต status ของ agent (ANALYZING, ERROR ฯลฯ)"""
        if name not in self._state["agents"]:
            self._state["agents"][name] = {"name": name, "status": status}
        else:
            self._state["agents"][name]["status"] = status

    def _setup_routes(self) -> None:
        app = self.app

        @app.get("/", response_class=HTMLResponse)
        async def root():
            """serve หน้า dashboard HTML"""
            try:
                with open("dashboard/index.html", encoding="utf-8") as f:
                    return HTMLResponse(content=f.read())
            except FileNotFoundError:
                return HTMLResponse(content="<h1>Dashboard loading...</h1>")

        @app.get("/health")
        async def health():
            """Health check — DO load balancer หรือ uptime monitor ใช้ endpoint นี้"""
            stats = {}
            if self._db:
                try:
                    stats = await self._db.get_trade_stats()
                except Exception:
                    pass
            return {
                "status": "ok",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "uptime_sec": int(asyncio.get_event_loop().time()),
                "agents_active": len(self._state.get("agents", {})),
                "trading_enabled": os.getenv("TRADING_ENABLED", "false"),
                "trade_stats": stats,
                "python": sys.version.split()[0],
            }

        @app.get("/api/stats")
        async def get_stats():
            """สถิติ paper trade — win rate, total PnL, best/worst trade"""
            if not self._db:
                return {"error": "db not connected"}
            return await self._db.get_trade_stats()

        @app.get("/api/state")
        async def get_state():
            """REST endpoint สำหรับ initial state"""
            await self._refresh_state()
            return self._state

        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket สำหรับ real-time updates"""
            await websocket.accept()
            self._connections.append(websocket)
            logger.info(f"WebSocket connected ({len(self._connections)} total)")
            try:
                # ส่ง state ปัจจุบันทันทีเมื่อ connect
                await self._refresh_state()
                await websocket.send_json(self._state)
                # รอ message จาก client (ป้องกัน connection หลุด)
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                self._connections.remove(websocket)
                logger.info(f"WebSocket disconnected ({len(self._connections)} total)")
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                if websocket in self._connections:
                    self._connections.remove(websocket)

    _TAKER_FEE_PCT = 0.0005  # Binance Futures taker fee ~0.05% ต่อฝั่ง

    async def _refresh_state(self) -> None:
        """ดึงข้อมูลล่าสุดจาก DB และ exchange"""
        try:
            if self._db:
                self._state["trades"] = await self._db.get_recent_trades(20)
                self._state["balance_history"] = await self._db.get_balance_history(30)
                self._state["today_pnl"] = await self._db.get_today_pnl()
                self._state["trade_stats"] = await self._db.get_trade_stats()
                latest_decision = await self._db.get_latest_master_decision()
                if latest_decision and not self._state["master_decision"]:
                    self._state["master_decision"] = latest_decision

                # คำนวณ unrealized PnL ของ open paper positions
                if self._data_fetcher:
                    open_trades = await self._db.get_open_trades()
                    paper_positions = []
                    for t in open_trades:
                        try:
                            asset = t.get("asset") or os.getenv("TRADING_SYMBOL", "ETH/USDT:USDT")
                            current_price = await self._data_fetcher.get_current_price(asset)
                            entry = float(t.get("entry_price", 0) or 0)
                            size  = float(t.get("size", 0) or 0)
                            side  = t.get("side", "LONG")
                            if not entry or not size:
                                continue
                            gross = (current_price - entry) * size if side == "LONG" else (entry - current_price) * size
                            # ค่าธรรมเนียมฝั่งปิด (ฝั่งเปิดถูกหักไปแล้วตอน open_long/open_short)
                            fee = (entry + current_price) * size * self._TAKER_FEE_PCT
                            unrealized = round(gross - fee, 4)
                            paper_positions.append({
                                "trade_id": t["id"],
                                "asset": asset,
                                "side": side,
                                "entry_price": entry,
                                "current_price": current_price,
                                "size": size,
                                "unrealized_pnl": unrealized,
                                "timestamp": t.get("timestamp", ""),
                            })
                        except Exception as e:
                            logger.warning(f"Dashboard: unrealized PnL error trade#{t.get('id')}: {e}")
                    self._state["paper_positions"] = paper_positions

            if self._data_fetcher:
                try:
                    self._state["balance"] = await self._data_fetcher.get_balance()
                    positions = await self._data_fetcher.get_open_positions()
                    self._state["position"] = positions[0] if positions else None
                except Exception as e:
                    logger.warning(f"Dashboard: exchange fetch error: {e}")

            self._state["last_update"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            logger.error(f"Dashboard._refresh_state error: {e}")

    async def broadcast_loop(self) -> None:
        """
        Push update ไปยังทุก WebSocket client ทุก 5 วินาที
        รัน concurrently กับ trading loop ใน main.py
        """
        while True:
            try:
                await self._refresh_state()
                if self._connections:
                    dead = []
                    for ws in self._connections:
                        try:
                            await ws.send_json(self._state)
                        except Exception:
                            dead.append(ws)
                    for ws in dead:
                        self._connections.remove(ws)
            except Exception as e:
                logger.error(f"Dashboard broadcast error: {e}")
            await asyncio.sleep(5)
