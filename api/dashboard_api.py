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

import pandas as pd

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ctrader_bot.config import load_secrets, load_settings
from ctrader_bot.journal.store import Journal
from ctrader_bot.mcp_client import CTraderMCPClient
from ctrader_bot.analysis.predictor import predict_next
from ctrader_bot.strategy.strategies import list_strategies, default_strategy_name
from ctrader_bot.strategy.sessions import session_windows
from ctrader_bot.training.optimizer import optimize as optimize_run, _fetch_data, _prepare
from ctrader_bot.training.simulator import simulate as simulate_run, append_simulated_to_registry
from ctrader_bot.training.registry import ParameterRegistry
from ctrader_bot.backtest.engine import prepare_backtest_bars

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
STATE = {"bars": [], "account": {}, "positions": [], "updated_at": None, "auto": None}
subscribers: list[WebSocket] = []

TRAINING = {
    "status": "idle",
    "mode": None,
    "stage": None,
    "progress": "",
    "log": [],
    "result": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
AUTO = {"enabled": False, "strategy": default_strategy_name(), "use_trained": False}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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
            if AUTO["enabled"]:
                try:
                    enriched = await _fetch_enriched_bars(SYMBOL, TIMEFRAME, 3)
                    if enriched:
                        pred = predict_next(pd.DataFrame(enriched), strategy_name=AUTO["strategy"],
                                             use_trained=AUTO["use_trained"])
                        STATE["auto"] = pred.to_dict()
                except Exception as e:
                    STATE["auto"] = {"direction": "FLAT", "source": "error", "note": str(e)}
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


# ── Strategies / sessions / registry ───────────────────────────────────────

@app.get("/api/strategies")
async def get_strategies():
    return {"strategies": list_strategies(), "default": default_strategy_name()}


@app.get("/api/sessions")
async def get_sessions():
    return {"sessions": [w.to_dict() for w in session_windows()]}


@app.get("/api/registry")
async def get_registry():
    """Expose the persistent parameter registry (best params + live feedback)."""
    return ParameterRegistry().export()


# ── Enriched bars for the chart / orderflow view ───────────────────────────

async def _fetch_enriched_bars(symbol: str, timeframe: str, days: int) -> list[dict]:
    """Fetch signal + profile bars via MCP and enrich with regime/levels."""
    settings = load_settings()
    profile_tf = settings["timeframes"]["profile"]
    async with CTraderMCPClient(SECRETS.ctrader_mcp_url) as client:
        signal_bars, profile_bars, symbol_details, _ = await _fetch_data(
            symbol, timeframe, profile_tf, days, client
        )
    if not signal_bars or not profile_bars:
        return []
    bars = _prepare(signal_bars, profile_bars, symbol_details, settings)
    records = []
    for _, row in bars.iterrows():
        records.append({
            "timestamp": row["timestamp"].isoformat() if hasattr(row["timestamp"], "isoformat") else str(row["timestamp"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0.0)),
            "regime": str(row.get("regime", "UNKNOWN")),
            "poc_prev": None if pd.isna(row.get("poc_prev")) else float(row["poc_prev"]),
            "vah_prev": None if pd.isna(row.get("vah_prev")) else float(row["vah_prev"]),
            "val_prev": None if pd.isna(row.get("val_prev")) else float(row["val_prev"]),
        })
    return records


@app.get("/api/bars")
async def get_bars(days: int = 3, timeframe: str = "M5"):
    symbol = SETTINGS.get("symbol", "US500")
    return {"symbol": symbol, "timeframe": timeframe, "bars": await _fetch_enriched_bars(symbol, timeframe, days)}


# ── Training jobs (historical optimize, then simulated trades) ────────────

@app.post("/api/training")
async def start_training(payload: dict):
    global TRAINING
    if TRAINING["status"] in ("running",):
        return {"started": False, "reason": "a training job is already running", "state": TRAINING}
    mode = payload.get("mode", "optimize")
    if mode not in ("optimize", "simulate"):
        return {"started": False, "reason": "mode must be optimize or simulate"}
    TRAINING = {
        "status": "running",
        "mode": mode,
        "stage": "queued",
        "progress": f"Starting {mode} training",
        "log": [f"[{_now()}] queued {mode} training (days={payload.get('days', 30)})"],
        "result": None,
        "error": None,
        "started_at": _now(),
        "finished_at": None,
    }
    asyncio.create_task(_run_training_job(
        mode=mode,
        days=int(payload.get("days", 30)),
        symbol=payload.get("symbol"),
        include_live=bool(payload.get("include_live", False)),
        top=int(payload.get("top", 10)),
        min_improvement=float(payload.get("min_improvement", 5.0)),
    ))
    return {"started": True, "state": TRAINING}


@app.get("/api/training")
async def training_status():
    return TRAINING


async def _run_training_job(mode: str, days: int, symbol: str | None,
                           include_live: bool, top: int, min_improvement: float) -> None:
    global TRAINING
    settings = load_settings()
    sym = symbol or settings["symbol"]
    try:
        if mode == "optimize":
            TRAINING["stage"] = "fetching historical data"
            TRAINING["log"].append(f"[{_now()}] fetching ~{days}d of historical bars")
            top_df = await optimize_run(days=days, symbol=sym, top_n=top, include_live=include_live)
            if top_df is not None and not top_df.empty:
                row = top_df.iloc[0].to_dict()
                params = {k: row.get(k) for k in (
                    "level_proximity_atr_mult", "breakout_confirm_atr_mult",
                    "trend_direction_lookback", "risk_per_trade_pct", "min_stop_atr_mult",
                )}
                metrics = {
                    "total_return_pct": row.get("total_return_pct"),
                    "max_drawdown_pct": row.get("max_drawdown_pct"),
                    "win_rate": row.get("win_rate"),
                    "n_trades": int(row.get("n_trades", 0)),
                }
                ParameterRegistry().save_best_params(params, metrics, source="optimize")
                TRAINING["result"] = {
                    "mode": "optimize",
                    "top_params": params,
                    "composite_score": row.get("composite_score"),
                    "metrics": metrics,
                }
                TRAINING["log"].append(f"[{_now()}] optimize done — best composite={row.get('composite_score')}")
            else:
                TRAINING["result"] = {"mode": "optimize", "note": "no results"}
        elif mode == "simulate":
            TRAINING["stage"] = "simulating trades"
            TRAINING["log"].append(f"[{_now()}] replaying ~{days}d of bars")
            df, report = await simulate_run(days=days, symbol=sym, analyze_failures=True)
            n = append_simulated_to_registry(df)
            wins = int((df["pnl"] > 0).sum()) if df is not None and not df.empty else 0
            total = int(len(df)) if df is not None and not df.empty else 0
            TRAINING["result"] = {
                "mode": "simulate",
                "n_trades": total,
                "win_rate": round(wins / total, 4) if total else 0.0,
                "registry_feedback_appended": n,
                "has_failure_report": bool(report),
            }
            TRAINING["log"].append(f"[{_now()}] simulate done — {total} trades, {n} appended to registry feedback")
        TRAINING["status"] = "completed"
        TRAINING["stage"] = "done"
    except Exception as e:
        TRAINING["status"] = "failed"
        TRAINING["error"] = str(e)
        TRAINING["log"].append(f"[{_now()}] ERROR {e}")
    finally:
        TRAINING["finished_at"] = _now()
        await broadcast({"type": "training", **TRAINING})


# ── Auto-mode analysis (strategy + trained data -> next-5min plan) ────────

@app.post("/api/auto/set")
async def set_auto(payload: dict):
    AUTO["enabled"] = bool(payload.get("enabled", False))
    if payload.get("strategy"):
        AUTO["strategy"] = payload["strategy"]
    AUTO["use_trained"] = bool(payload.get("use_trained", False))
    return {"auto": dict(AUTO)}


@app.get("/api/analysis")
async def get_analysis(strategy: str | None = None, use_trained: bool = False):
    sym = SETTINGS.get("symbol", "US500")
    tf = SETTINGS.get("timeframes", {}).get("signal", "M5")
    bars = await _fetch_enriched_bars(sym, tf, 3)
    if not bars:
        return {"direction": "FLAT", "source": "no-data", "note": "no bars from MCP"}
    df = pd.DataFrame(bars)
    pred = predict_next(df, strategy_name=strategy, use_trained=use_trained)
    return pred.to_dict()


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
