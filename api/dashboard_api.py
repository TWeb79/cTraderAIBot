"""
Local API/WebSocket bridge between the trading loop and the dashboard UI.

Serves on port 8158 (project 58). The dashboard UI (port 8058) reads from
this API. The browser never talks to the cTrader MCP server or Ollama
directly — only this process does.

Run:
    uvicorn api.dashboard_api:app --host 0.0.0.0 --port 8158
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ctrader_bot.config import load_secrets, load_settings
from ctrader_bot.journal.store import Journal
from ctrader_bot.mcp_client import CTraderMCPClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]

app = FastAPI(title="cTrader Bot Dashboard API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SETTINGS = load_settings()
SECRETS = load_secrets()
SYMBOL = SETTINGS.get("symbol", "US500")
TIMEFRAME = SETTINGS.get("timeframes", {}).get("signal", "M5")
BARS_FOR_CONTEXT = SETTINGS.get("execution", {}).get("bars_for_context", 100)
DB_PATH = str(PROJECT_ROOT / "trade_journal.sqlite3")

mcp: Optional[CTraderMCPClient] = None
journal = Journal(DB_PATH)
STATE = {"bars": [], "account": {}, "positions": [], "updated_at": None}
subscribers: list[WebSocket] = []


@app.on_event("startup")
async def startup():
    global mcp
    mcp = CTraderMCPClient(SECRETS.ctrader_mcp_url)
    try:
        await mcp.__aenter__()
    except Exception as e:
        print(f"[warn] MCP connection failed on startup: {e}")
        mcp = None
    asyncio.create_task(refresh_loop())


@app.on_event("shutdown")
async def shutdown():
    global mcp
    if mcp is not None:
        await mcp.__aexit__(None, None, None)


async def refresh_loop(interval_seconds: int = 15):
    """Keeps STATE current for the dashboard, independent of the trading
    loop's own decision cadence."""
    while True:
        try:
            if mcp is None:
                await asyncio.sleep(interval_seconds)
                continue

            from_dt = __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - __import__("datetime").timedelta(days=1)
            to_dt = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            bars_raw = await mcp.get_trendbars(SYMBOL, TIMEFRAME, from_dt, to_dt, limit=BARS_FOR_CONTEXT)
            bars = [
                {"timestamp": b.timestamp.isoformat(), "open": b.open, "high": b.high,
                 "low": b.low, "close": b.close, "volume": b.volume}
                for b in bars_raw
            ]

            account_raw = await mcp.get_balance()
            positions_raw = await mcp.get_positions()
            positions = positions_raw if isinstance(positions_raw, list) else positions_raw.get("positions", [])

            STATE.update(
                bars=bars,
                account={
                    "equity": account_raw.get("equity", account_raw.get("balance", 0)),
                    "balance": account_raw.get("balance", 0),
                    "daily_pnl": account_raw.get("daily_pnl", 0.0),
                },
                positions=positions,
                updated_at=time.time(),
            )
            await broadcast({"type": "snapshot", **STATE})
        except Exception as e:
            await broadcast({"type": "error", "message": str(e)})
        await asyncio.sleep(interval_seconds)


async def broadcast(message: dict):
    dead = []
    for ws in subscribers:
        try:
            await ws.send_text(json.dumps(message, default=str))
        except Exception:
            dead.append(ws)
    for ws in dead:
        subscribers.remove(ws)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "mcp_connected": mcp is not None,
        "demo_mode": SECRETS.demo_mode,
    }


@app.get("/api/registry")
async def get_registry():
    """Expose the persistent parameter registry (best params + live feedback)."""
    from ctrader_bot.training.registry import ParameterRegistry

    return ParameterRegistry().export()


@app.get("/api/version")
async def version():
    return {
        "version": "0.1.0",
        "build_time": "2026-08-19",
    }


@app.get("/api/state")
async def get_state():
    return STATE


@app.get("/api/journal")
async def get_journal(limit: int = 25):
    return journal.get_trades(limit=limit)


@app.get("/api/digest")
async def get_digest():
    return {
        "digest": journal.latest_digest(),
        "stats": journal.aggregate_stats(),
    }


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    subscribers.append(websocket)
    try:
        await websocket.send_text(json.dumps({"type": "snapshot", **STATE}, default=str))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        subscribers.remove(websocket)
