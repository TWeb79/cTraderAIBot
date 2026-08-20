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
from ctrader_bot.strategy.sessions import session_windows, session_markers
from ctrader_bot.training.optimizer import optimize as optimize_run, _fetch_data, _prepare
from ctrader_bot.training.simulator import simulate as simulate_run, append_simulated_to_registry
from ctrader_bot.training.registry import ParameterRegistry
from ctrader_bot.backtest.engine import prepare_backtest_bars

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Same path execution/live_runner.py reads via load_auto_control() — this is
# the file-based control channel that lets the dashboard's Auto Mode toggle
# and strategy selector actually gate the live-trading loop (a separate
# process), not just the analysis-panel prediction. See implementationplan.md
# §10.6 / §11.8.
AUTO_CONTROL_PATH = PROJECT_ROOT / "data" / "cache" / ".auto_control.json"
# Same convention, read by live_runner.py's check_kill_switch().
KILL_SWITCH_PATH = PROJECT_ROOT / "data" / "cache" / ".kill_switch"
# Same convention, consumed by live_runner.py's consume_manual_trade_request()
# — the dashboard's one-click "execute predicted trade" button queues a
# request here rather than placing the order itself; see POST /api/manual-trade.
MANUAL_TRADE_REQUEST_PATH = PROJECT_ROOT / "data" / "cache" / ".manual_trade_request.json"

# Bump both of these with every batch of changes that reaches the dashboard
# — kept in sync with implementationplan.md's own **Version:** header so the
# app version visibly moves instead of sitting frozen at "0.1.0" forever.
APP_VERSION = "0.7.4"
APP_BUILD_TIME = "2026-08-20"

app = FastAPI(title="cTrader Bot Dashboard API", version=APP_VERSION)

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
# Populated after a "simulate" training job completes — the full trade list
# (predicted direction/price vs actual outcome) + aggregate stats behind the
# Training panel's "simulated trades vs prediction" analysis window. See
# _simulation_trade_records()/_simulation_summary() and GET /api/training/trades.
LAST_SIMULATION: dict = {"trades": [], "summary": None, "finished_at": None}
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
            # Always compute the prediction, not just when auto-trading is
            # switched on: the "current prediction" panel and the one-click
            # manual-execute button both need a live prediction regardless of
            # AUTO["enabled"] — that flag only controls whether the live
            # runner acts on signals automatically, not whether the read-only
            # prediction itself is computed.
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


@app.get("/api/version")
async def version():
    return {
        "version": APP_VERSION,
        "build_time": APP_BUILD_TIME,
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


@app.get("/api/registry/history")
async def get_registry_history(limit: int = 20):
    """Optimization-history time series, for the 'model is learning' chart.

    Deterministic — this is a view over ``ParameterRegistry.optimization_history``
    (grid-search / retrain runs), not live model inference. See
    implementationplan.md §11.4 for why this replaces a literal NN visualization.
    """
    registry = ParameterRegistry()
    return {
        "history": registry.get_optimization_history(limit=limit),
        "live_feedback": registry.get_live_feedback_summary(),
        "performance": registry.get_performance(),
    }


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
            # Session-split volume profile + extra training datapoints
            # (implementationplan.md §11.1): pre-NY (Asia+Frankfurt) vs NY
            # portions of the *prior* session, plus prior day-close / NY-open
            # reference prices.
            "poc_pre_ny_prev": None if pd.isna(row.get("poc_pre_ny_prev")) else float(row["poc_pre_ny_prev"]),
            "vah_pre_ny_prev": None if pd.isna(row.get("vah_pre_ny_prev")) else float(row["vah_pre_ny_prev"]),
            "val_pre_ny_prev": None if pd.isna(row.get("val_pre_ny_prev")) else float(row["val_pre_ny_prev"]),
            "poc_ny_prev": None if pd.isna(row.get("poc_ny_prev")) else float(row["poc_ny_prev"]),
            "vah_ny_prev": None if pd.isna(row.get("vah_ny_prev")) else float(row["vah_ny_prev"]),
            "val_ny_prev": None if pd.isna(row.get("val_ny_prev")) else float(row["val_ny_prev"]),
            "ny_open_price_prev": None if pd.isna(row.get("ny_open_price_prev")) else float(row["ny_open_price_prev"]),
            "day_close_price_prev": None if pd.isna(row.get("day_close_price_prev")) else float(row["day_close_price_prev"]),
        })
    return records


@app.get("/api/bars")
async def get_bars(days: int = 3, timeframe: str = "M5"):
    symbol = SETTINGS.get("symbol", "US500")
    bars = await _fetch_enriched_bars(symbol, timeframe, days)
    markers: list[dict] = []
    if bars:
        from datetime import datetime as _dt
        start = _dt.fromisoformat(bars[0]["timestamp"])
        end = _dt.fromisoformat(bars[-1]["timestamp"])
        markers = session_markers(start, end)
    return {"symbol": symbol, "timeframe": timeframe, "bars": bars, "session_markers": markers}


_TIMEFRAME_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def _footprint_from_sub_bars(sub_bars: list, pip_size: float, bin_ticks: float) -> dict:
    """Pure bucketing logic behind GET /api/bars/{timestamp}/footprint —
    split out so it's testable without an MCP connection or FastAPI TestClient.
    See that endpoint's docstring for the honest-proxy rationale.
    """
    bin_size = max(bin_ticks * pip_size, 1e-9)

    buckets: dict[float, dict] = {}
    for b in sub_bars:
        mid = (b.high + b.low) / 2
        level = round(mid / bin_size) * bin_size
        entry = buckets.setdefault(level, {"buy_volume": 0.0, "sell_volume": 0.0})
        if b.close >= b.open:
            entry["buy_volume"] += b.volume
        else:
            entry["sell_volume"] += b.volume

    levels = [
        {"price": price, "buy_volume": round(v["buy_volume"], 4), "sell_volume": round(v["sell_volume"], 4),
         "delta": round(v["buy_volume"] - v["sell_volume"], 4)}
        for price, v in sorted(buckets.items(), reverse=True)
    ]
    total_buy = sum(l["buy_volume"] for l in levels)
    total_sell = sum(l["sell_volume"] for l in levels)
    high_demand = max(levels, key=lambda l: l["buy_volume"] + l["sell_volume"]) if levels else None

    return {
        "pip_size": pip_size,
        "bin_size": bin_size,
        "levels": levels,
        "total_buy_volume": round(total_buy, 4),
        "total_sell_volume": round(total_sell, 4),
        "high_demand_price": high_demand["price"] if high_demand else None,
        "note": ("Tick-volume proxy (each sub-bar classified by close-vs-open direction), "
                 "not true bid/ask order-book depth — this MCP server exposes no DOM/Level-2 tool."),
    }


@app.get("/api/bars/{timestamp}/footprint")
async def get_bar_footprint(timestamp: str, timeframe: str = "M5"):
    """Per-candle "footprint" — buy-side vs sell-side tick-volume by price
    level within the given candle (implementationplan.md §15.2, the
    "zooming into the candle with the bids and asks" request).

    This is NOT true Level-2 bid/ask depth: the cTrader MCP server exposes no
    such tool (see mcp_cheatsheet.md / mcp_client.py's full method list —
    there is no order-book/DOM call), so an honest proxy is built instead
    from the finer M1 sub-bars that make up this candle: each M1 bar's
    volume is classified "buy" if it closed above its open and "sell" if
    below, then bucketed into the same price-bin size the session volume
    profile uses. The bucket with the most combined volume is reported as
    high_demand_price — the closest available answer to "identify a high
    demand" level without genuine order-book data.
    """
    from datetime import datetime as _dt, timedelta as _td

    try:
        candle_start = _dt.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return {"error": "invalid timestamp; expected ISO-8601"}

    if mcp is None:
        return {"error": "MCP not connected"}

    tf_minutes = _TIMEFRAME_MINUTES.get(timeframe, 5)
    candle_end = candle_start + _td(minutes=tf_minutes)
    profile_tf = SETTINGS.get("timeframes", {}).get("profile", "M1")

    sub_bars = await mcp.get_trendbars(SYMBOL, profile_tf, candle_start, candle_end, limit=200)
    if not sub_bars:
        return {
            "timestamp": timestamp, "timeframe": timeframe, "levels": [],
            "note": "no sub-bars found for this candle (outside available history, or too recent)",
        }

    symbol_details = await mcp.get_symbol_details(SYMBOL)
    pip_size = symbol_details.get("pipSize", 1.0)
    bin_ticks = SETTINGS.get("volume_profile", {}).get("price_bin_ticks", 5)

    result = _footprint_from_sub_bars(sub_bars, pip_size, bin_ticks)
    return {"timestamp": timestamp, "timeframe": timeframe, **result}


def _footprints_by_candle(signal_bars: list, profile_bars: list, timeframe: str,
                          pip_size: float, bin_ticks: float) -> dict[str, dict]:
    """Bulk version of `_footprint_from_sub_bars` — one footprint per
    signal-timeframe candle, computed from a single already-fetched
    `profile_bars` list instead of one MCP round trip per candle. Backs the
    chart's Orderflow view (implementationplan.md §15.2 follow-up: "the
    orderflow footprint should be shown instead of a candle once i activate
    this view"), which needs every visible candle's footprint at once, not
    just the one the user clicks.
    """
    import bisect
    from datetime import timedelta as _td

    tf_minutes = _TIMEFRAME_MINUTES.get(timeframe, 5)
    window = _td(minutes=tf_minutes)
    sorted_profile = sorted(profile_bars, key=lambda b: b.timestamp)
    profile_timestamps = [b.timestamp for b in sorted_profile]

    result: dict[str, dict] = {}
    for sb in signal_bars:
        start = sb.timestamp
        end = start + window
        lo = bisect.bisect_left(profile_timestamps, start)
        hi = bisect.bisect_left(profile_timestamps, end)
        sub_bars = sorted_profile[lo:hi]
        if not sub_bars:
            continue
        ts_key = sb.timestamp.isoformat() if hasattr(sb.timestamp, "isoformat") else str(sb.timestamp)
        result[ts_key] = _footprint_from_sub_bars(sub_bars, pip_size, bin_ticks)
    return result


@app.get("/api/bars/footprint")
async def get_bars_footprint(days: int = 3, timeframe: str = "M5"):
    """Bulk per-candle orderflow footprint for every candle GET /api/bars
    would return over the same days/timeframe — one MCP fetch (mirroring
    `_fetch_enriched_bars`'s own signal+profile fetch), not N fetches for N
    visible candles. Powers the chart's Orderflow view directly; the
    single-candle GET /api/bars/{timestamp}/footprint above remains for the
    click-to-inspect-exact-numbers sidebar panel.
    """
    symbol = SETTINGS.get("symbol", "US500")
    settings = load_settings()
    profile_tf = settings.get("timeframes", {}).get("profile", "M1")

    async with CTraderMCPClient(SECRETS.ctrader_mcp_url) as client:
        signal_bars, profile_bars, symbol_details, _ = await _fetch_data(symbol, timeframe, profile_tf, days, client)

    if not signal_bars or not profile_bars:
        return {"symbol": symbol, "timeframe": timeframe, "footprints": {}}

    pip_size = symbol_details.get("pipSize", 1.0)
    bin_ticks = settings.get("volume_profile", {}).get("price_bin_ticks", 5)
    footprints = _footprints_by_candle(signal_bars, profile_bars, timeframe, pip_size, bin_ticks)
    return {"symbol": symbol, "timeframe": timeframe, "footprints": footprints}


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


@app.get("/api/training/trades")
async def get_training_trades(limit: int = 50):
    """Simulated trades from the most recent "simulate" training job, each
    with its predicted direction/price (from the deterministic signal at
    entry) alongside the actual outcome — feeds the Training panel's
    "simulated trades vs prediction" analysis window."""
    trades = LAST_SIMULATION.get("trades") or []
    return {
        "trades": trades[:limit],
        "summary": LAST_SIMULATION.get("summary"),
        "finished_at": LAST_SIMULATION.get("finished_at"),
    }


def _simulation_trade_records(df: pd.DataFrame, limit: int = 200) -> list[dict]:
    """Most-recent-first trade records for the "simulated trades vs
    prediction" analysis window: each simulated trade's predicted direction
    (side) and predicted price (target_price, renamed here) alongside the
    actual outcome (exit_price/exit_reason/r_multiple)."""
    if df is None or df.empty:
        return []
    records = []
    for _, row in df.tail(limit).iloc[::-1].iterrows():
        entry_price = row.get("entry_price")
        target_price = row.get("target_price")
        exit_price = row.get("exit_price")
        price_delta = None
        if pd.notna(exit_price) and pd.notna(target_price):
            price_delta = float(exit_price) - float(target_price)
        records.append({
            "entry_time": str(row.get("entry_time")) if pd.notna(row.get("entry_time")) else None,
            "exit_time": str(row.get("exit_time")) if pd.notna(row.get("exit_time")) else None,
            "side": row.get("side"),
            "setup_tag": row.get("setup_tag"),
            "regime": row.get("regime"),
            "entry_price": None if pd.isna(entry_price) else float(entry_price),
            "predicted_price": None if pd.isna(target_price) else float(target_price),
            "exit_price": None if pd.isna(exit_price) else float(exit_price),
            "exit_reason": row.get("exit_reason"),
            "price_delta": price_delta,
            "r_multiple": None if pd.isna(row.get("r_multiple")) else float(row.get("r_multiple")),
            "direction_correct": row.get("exit_reason") == "target",
        })
    return records


def _simulation_summary(df: pd.DataFrame) -> dict:
    if df is None or df.empty:
        return {"n_trades": 0}
    n = len(df)
    hits = int((df["exit_reason"] == "target").sum())
    deltas = (df["exit_price"] - df["target_price"]).dropna()
    by_regime: dict[str, dict] = {}
    for regime, group in df.groupby("regime"):
        g_n = len(group)
        g_hits = int((group["exit_reason"] == "target").sum())
        by_regime[str(regime)] = {
            "n_trades": g_n,
            "direction_hit_rate": round(g_hits / g_n, 4) if g_n else 0.0,
            "avg_r_multiple": round(float(group["r_multiple"].mean()), 4) if g_n else 0.0,
        }
    return {
        "n_trades": n,
        "direction_hit_rate": round(hits / n, 4) if n else 0.0,
        "avg_price_delta": round(float(deltas.mean()), 5) if len(deltas) else None,
        "avg_abs_price_delta": round(float(deltas.abs().mean()), 5) if len(deltas) else None,
        "avg_r_multiple": round(float(df["r_multiple"].mean()), 4) if n else 0.0,
        "by_regime": by_regime,
    }


async def _run_training_job(mode: str, days: int, symbol: str | None,
                           include_live: bool, top: int, min_improvement: float) -> None:
    global TRAINING, LAST_SIMULATION
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
                # Guard against silently persisting a broken registry entry
                # again (see training/optimizer.py's _run_backtest_sync —
                # this exact shape-mismatch bug used to make every value
                # here None while still reporting "optimize done"). If the
                # top result row is missing every param column, something
                # upstream changed shape; surface it as a failure instead
                # of writing null best_params that --use-trained-params /
                # the dashboard's "use trained" toggle would then silently
                # no-op against.
                if all(v is None for v in params.values()):
                    raise RuntimeError(
                        "optimize() returned a top result with no usable parameter "
                        "columns (all None) — not saving to the registry. This means "
                        "training/optimizer.py's result shape changed; see "
                        "_run_backtest_sync()."
                    )
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
            LAST_SIMULATION = {
                "trades": _simulation_trade_records(df),
                "summary": _simulation_summary(df),
                "finished_at": _now(),
            }
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

@app.get("/api/auto")
async def get_auto():
    return {"auto": dict(AUTO)}


@app.post("/api/auto/set")
async def set_auto(payload: dict):
    AUTO["enabled"] = bool(payload.get("enabled", False))
    if payload.get("strategy"):
        AUTO["strategy"] = payload["strategy"]
    AUTO["use_trained"] = bool(payload.get("use_trained", False))

    # Persist to the file-based control channel the (separate-process) live
    # runner polls each cycle — see AUTO_CONTROL_PATH above.
    try:
        AUTO_CONTROL_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUTO_CONTROL_PATH.write_text(json.dumps(dict(AUTO)))
    except OSError as e:
        print(f"[warn] failed to write auto-control file: {e}")

    await broadcast({"type": "auto", "auto": dict(AUTO)})
    return {"auto": dict(AUTO)}


# ── Kill switch (dashboard-driven) ──────────────────────────────────────────
#
# execution/live_runner.py's create_kill_switch()/check_kill_switch() have
# always existed but were only reachable by a human `touch`ing the file
# directly — no dashboard control. This closes that gap: the same
# convention (create/remove data/cache/.kill_switch), now toggleable from
# the dashboard too. Reading the file directly here (KILL_SWITCH_PATH, same
# as GET /api/manual-trade already did) rather than importing
# execution.live_runner keeps this process from pulling in that module's
# MCP-client/async-loop machinery just to touch a file.

@app.get("/api/kill-switch")
async def get_kill_switch():
    return {"active": KILL_SWITCH_PATH.exists()}


@app.post("/api/kill-switch/set")
async def set_kill_switch(payload: dict):
    active = bool(payload.get("active", False))
    try:
        KILL_SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        if active:
            KILL_SWITCH_PATH.touch()
        else:
            KILL_SWITCH_PATH.unlink(missing_ok=True)
    except OSError as e:
        return {"active": KILL_SWITCH_PATH.exists(), "error": str(e)}
    now_active = KILL_SWITCH_PATH.exists()
    await broadcast({"type": "kill_switch", "active": now_active})
    return {"active": now_active}


# ── Live-cycle diagnostics ("why isn't it trading?") ────────────────────────
#
# execution/live_runner.py writes a small snapshot to
# data/cache/.last_cycle_status.json after every cycle decision point (kill
# switch, no data from MCP, no signal, auto-mode gating, sizing failures,
# order placement, ...) — see _write_cycle_status() there. This just reads
# it back, best-effort, same degrade-to-"nothing yet" pattern as the other
# file-based IPC in this module.
LAST_CYCLE_STATUS_PATH = PROJECT_ROOT / "data" / "cache" / ".last_cycle_status.json"


@app.get("/api/live-status")
async def get_live_status():
    try:
        data = json.loads(LAST_CYCLE_STATUS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {
            "available": False,
            "reason": "no cycle status recorded yet — the live runner process "
                      "may not be running (see README's Usage/Docker sections)",
        }
    return {"available": True, **data}


# ── Manual trade execution ("execute predicted trade" — one OK button) ─────
#
# This process never places orders itself (see the module docstring — only
# live_runner.py talks to MCP for writes). Clicking OK queues a request via
# the same file-based IPC pattern already used for auto-mode, using this
# server's own current prediction (STATE["auto"]) as the source of truth for
# direction/entry/stop/target rather than trusting whatever the client sends
# — a stale button click can't fire off outdated prices this way. The target
# price *is* the take-profit, per the feature request: predict_next()'s
# `target` becomes the trade's TP directly.

@app.post("/api/manual-trade")
async def submit_manual_trade():
    if KILL_SWITCH_PATH.exists():
        return {"submitted": False, "reason": "kill switch is active — no new trades"}
    if MANUAL_TRADE_REQUEST_PATH.exists():
        return {"submitted": False, "reason": "a manual trade request is already pending execution"}

    pred = STATE.get("auto")
    if not pred or pred.get("direction") not in ("LONG", "SHORT"):
        return {"submitted": False, "reason": "no actionable prediction available right now"}
    if pred.get("entry") is None or pred.get("stop") is None or pred.get("target") is None:
        return {"submitted": False, "reason": "prediction is missing entry/stop/target"}

    request = {
        "direction": pred["direction"],
        "entry": pred["entry"],
        "stop": pred["stop"],
        "target": pred["target"],
        "reason": f"manual-dashboard:{pred.get('reason', 'unknown')}",
        "requested_at": _now(),
    }
    try:
        MANUAL_TRADE_REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANUAL_TRADE_REQUEST_PATH.write_text(json.dumps(request))
    except OSError as e:
        return {"submitted": False, "reason": f"failed to queue request: {e}"}
    return {"submitted": True, "request": request}


@app.get("/api/manual-trade")
async def get_manual_trade_status():
    """Lets the dashboard show "pending execution..." until live_runner.py
    picks up and consumes the request file (typically within one poll
    cycle). Once it's gone, either it executed (check /api/state's
    positions / the journal) or the live runner isn't running."""
    pending = MANUAL_TRADE_REQUEST_PATH.exists()
    request = None
    if pending:
        try:
            request = json.loads(MANUAL_TRADE_REQUEST_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            request = None
    return {"pending": pending, "request": request, "kill_switch_active": KILL_SWITCH_PATH.exists()}


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
