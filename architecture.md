# Architecture — cTrader Anthropic Bot (Project 58)

**Version:** 0.1.0  
**Author:** Inventions4All - github:TWeb79

---

## System Structure

```
┌─────────────────────────────────────────────────────────────────┐
│                      Project 58 - cTrader Bot                    │
│                                                                 │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │ cTrader MCP │───▶│ Live Runner  │───▶│  Risk Manager    │  │
│  │ Server      │    │ (main.py)    │    │  (hard gates)    │  │
│  │ :9876       │    └──────┬───────┘    └──────────────────┘  │
│  └─────────────┘           │                                    │
│                             ▼                                    │
│  ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │ Backtest    │───▶│ Backtest     │───▶│  SQLite Journal  │  │
│  │ (historical)│    │ Runner       │    │  (local file)    │  │
│  └─────────────┘    └──────────────┘    └────────┬─────────┘  │
│                                                   │             │
│  ┌─────────────┐    ┌──────────────┐             │             │
│  │ FastAPI     │◀───│ Dashboard    │◀────────────┘             │
│  │ :8158       │    │ API          │                            │
│  └──────┬──────┘    └──────────────┘                            │
│         │                                                        │
│  ┌──────┴──────┐                                                │
│  │ Static UI   │                                                │
│  │ :8058       │                                                │
│  └─────────────┘                                                │
│                                                                 │
│  Optional: Anthropic API for journal digest                     │
│  (offline only, never in the trading loop)                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Module Responsibilities

| Module | Responsibility |
|---|---|
| `mcp_client.py` | Typed async client for the cTrader local MCP server. Handles session lifecycle, pagination, and demo-account assertions. |
| `config.py` | Loads `config/config.yaml` (strategy/runtime) and `.env` (secrets). Single source of truth for settings. |
| `indicators/regime.py` | Computes ADX/DI and classifies market regime (RANGE / BREAKOUT / TREND). |
| `indicators/volume_profile.py` | Builds session volume profiles (POC, VAH/VAL) from OHLCV bars. |
| `strategy/levels.py` | Computes prior-session levels and NY-open gap-fill state. |
| `strategy/signals.py` | Combines regime + levels into entry signals. Pure function, shared by backtest and live runner. |
| `risk/risk_manager.py` | Hard-enforced risk rules: position sizing, daily-loss circuit breaker, max open risk. |
| `backtest/engine.py` | Event-driven backtest using the same strategy and risk modules as live trading. |
| `backtest/report.py` | Performance report with daily-return distribution analysis. |
| `journal/store.py` | SQLite-backed trade history and strategy digest storage. |
| `execution/live_runner.py` | Main async loop: fetch data, evaluate strategy, risk gate, place order, poll, reflect. |
| `execution/backtest_runner.py` | CLI entry point for backtesting. |
| `api/dashboard_api.py` | FastAPI read-only API + WebSocket for dashboard updates. |
| `training/optimizer.py` | Offline parameter grid search using the backtest engine. |
| `training/simulator.py` | Offline bar-by-bar simulated trading with failure analysis. |
| `dashboard/` | Static HTML/CSS/JS dashboard served on port 8058. |

---

## Data Flow

### Live Trading

1. `scripts/run_live.py` → `execution/live_runner.py`
2. Connects to cTrader MCP via `mcp_client.CTraderMCPClient`
3. Each cycle: fetch bars + account + positions
4. `strategy.signals.evaluate_bar()` produces a `Signal` or `None`
5. If signal exists, `risk.risk_manager.RiskManager` sizes the trade
6. If approved, `mcp_client.place_market_order()` executes
7. Polls until position closes, records `TradeReflection` in SQLite
8. Every N trades, generates a strategy digest (optional, via Anthropic API offline)

### Backtesting

1. `scripts/run_backtest.py` → `execution/backtest_runner.py`
2. Fetches historical bars via `mcp_client`
3. `backtest.engine.prepare_backtest_bars()` enriches with regime, levels, ATR
4. `backtest.engine.run_backtest()` walks bars, uses same `evaluate_bar()` + `RiskManager`
5. `backtest.report.build_report()` surfaces daily-return distribution

### Dashboard

1. `api/dashboard_api.py` runs on port 8158
2. Background task refreshes state every 15s via MCP
3. WebSocket pushes snapshots to connected clients
4. Static files served on port 8058 read from API

### Training (Offline Only)

1. `scripts/run_training.py optimize` → `training/optimizer.py`
   - Fetches historical bars via MCP
   - Sweeps parameter grid using `backtest.engine.run_backtest()`
   - Outputs top-N CSV to `data/reports/`
2. `scripts/run_training.py simulate` → `training/simulator.py`
   - Replays historical bars bar-by-bar with in-memory simulated positions
   - Generates trade CSV + failure analysis markdown in `data/reports/`
3. Neither mechanism touches the live MCP or places orders.

---

## External Dependencies

| Dependency | Role |
|---|---|
| cTrader Desktop App + MCP Server | Market data, account info, order execution (port 9876) |
| SQLite | Trade journal and digest storage (local file) |
| Anthropic API (optional) | Offline journal digest generation |

---

## Port Allocation (Project 58)

| Port | Service |
|---|---|
| 8058 | Dashboard (static files) |
| 8158 | FastAPI dashboard API + WebSocket |
| 9876 | cTrader MCP server (external, not this project) |
