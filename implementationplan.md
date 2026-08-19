# Implementation Plan — cTrader Anthropic Bot (Project 58)

**Version:** 0.1.0  
**Date:** 2026-08-19  
**Author:** Inventions4All - github:TWeb79

---

## 1. Current State Assessment

### 1.1 Mixed Concepts Identified

| Concept | Location | Status |
|---|---|---|
| Deterministic volume-profile strategy | `src/ctrader_bot/strategy/`, `indicators/` | Functional, tested |
| Backtest engine | `src/ctrader_bot/backtest/` | Functional, tested |
| Risk manager | `src/ctrader_bot/risk/` | Functional, tested |
| MCP client (typed) | `src/ctrader_bot/mcp_client.py` | Functional |
| LLM orchestrator (Ollama) | `orchestrator.py` | Skeleton, conflicts with deterministic design |
| Dashboard API (FastAPI) | `dashboard_api.py` | Skeleton, imports from `orchestrator.py` |
| Dashboard UI (React/JSX) | `trading-cockpit.jsx` | Mock data, not wired |
| Anthropic journal review | Referenced in README | Missing (`scripts/run_journal_review.py`) |
| Config loader | `src/ctrader_bot/config.py` | Functional |
| Duplicate architecture docs | `architecture.md`, `ai-trader-architecture.md` | Redundant |

**Core conflict:** The project has two competing design philosophies:
1. **Deterministic strategy** — rules-based entry/exit using volume profile + regime classification (the actual tested code in `src/`)
2. **LLM-augmented loop** — Ollama predictor/reflector in `orchestrator.py` (skeleton, untested, contradicts "100% deterministic Python" stated in README)

### 1.2 Port Assignment (Project 58)

Per `RULES_ports.md`:
- **8058** — Web dashboard
- **8158** — FastAPI service (API + WebSocket)
- **8258** — Database/reserved
- **8958** — LLM/reserved

---

## 2. Unified Architecture Decision

**Single deterministic pipeline with optional LLM journal analysis.**

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

### 2.1 Design Principles

1. **Deterministic trading loop** — no LLM in the live trading path. All entry/exit decisions use the existing tested strategy code.
2. **Single source of truth for config** — `config/config.yaml` for strategy/runtime, `.env` for secrets only.
3. **Thin API layer** — `api/dashboard_api.py` exposes read-only snapshots + WebSocket. Never imports `orchestrator.py`.
4. **Separate execution modes** — `live_runner.py` and `backtest_runner.py` both use the same `strategy.signals.evaluate_bar()` and `risk.risk_manager.RiskManager`.
5. **Journal as shared state** — SQLite file accessed by both runners and the API.

---

## 3. File Structure (Target)

```
58-cTraderAnthropicBot/
├── README.md
├── ARCHITECTURE.md
├── RULES_coding.md
├── RULES_ports.md
├── implementationplan.md
├── pyproject.toml
├── requirements.txt
├── .env.example
├── .gitignore
├── config/
│   └── config.yaml
├── src/ctrader_bot/
│   ├── __init__.py
│   ├── config.py
│   ├── mcp_client.py
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── live_runner.py          # NEW: main async loop
│   │   └── backtest_runner.py      # NEW: wraps backtest engine
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── levels.py
│   │   └── signals.py
│   ├── indicators/
│   │   ├── __init__.py
│   │   ├── regime.py
│   │   └── volume_profile.py
│   ├── risk/
│   │   ├── __init__.py
│   │   └── risk_manager.py
│   ├── backtest/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   └── report.py
│   └── journal/
│       ├── __init__.py
│       └── store.py                 # NEW: SQLite abstraction
├── api/
│   ├── __init__.py
│   ├── dashboard_api.py             # MOVED + cleaned
│   └── schemas.py                   # NEW: Pydantic response models
├── dashboard/
│   ├── index.html
│   ├── css/
│   │   └── cockpit.css
│   └── js/
│       ├── app.js
│       ├── chart.js
│       └── api.js
├── scripts/
│   ├── discover_mcp_tools.py
│   ├── run_backtest.py
│   ├── run_live.py                  # NEW: entry point for live trading
│   └── run_journal_review.py        # NEW: optional Anthropic digest
├── tests/
│   ├── __init__.py
│   ├── test_levels.py
│   ├── test_regime.py
│   ├── test_signals.py
│   ├── test_volume_profile.py
│   ├── test_risk_manager.py
│   ├── test_backtest_engine.py
│   ├── test_journal_store.py        # NEW
│   └── test_live_runner.py          # NEW
└── data/
    ├── cache/
    │   ├── .gitkeep
    │   ├── last_backtest_equity_curve.csv
    │   └── last_backtest_trades.csv
    └── reports/
        └── .gitkeep
```

### 3.1 Files to Remove

| File | Reason |
|---|---|
| `orchestrator.py` | Superseded by `execution/live_runner.py` + `execution/backtest_runner.py` |
| `dashboard_api.py` (root) | Moved to `api/dashboard_api.py` |
| `ai-trader-architecture.md` | Duplicate of `architecture.md` |
| `trading-cockpit.jsx` | Replaced by vanilla HTML/CSS/JS dashboard |
| `src/ctrader_bot/execution/__init__.py` | Empty, will be repopulated |
| `src/ctrader_bot/journal/__init__.py` | Empty, will be repopulated |

---

## 4. Implementation Steps

### Phase 1: Core Extraction (No Behavior Changes)

**Step 1.1 — Create `journal/store.py`**
- Extract `Journal` class from `orchestrator.py`
- Add `get_trades(limit)` and `get_digest()` methods
- Add `record_cycle_state()` for crash recovery

**Step 1.2 — Create `execution/live_runner.py`**
- Extract `CTraderMCP` class from `orchestrator.py` → rename to `CTraderMCPClient` (align with `mcp_client.py` naming)
- Extract `summarize_bars()`, prompt builders, Ollama calls
- Extract `risk_gate()` → replace with `RiskManager` from `risk/risk_manager.py`
- Extract `run_one_cycle()` → use typed `mcp_client.py` methods
- Add `--dry-run` flag
- Add kill-switch file check
- Add crash recovery: reconcile positions on startup

**Step 1.3 — Create `execution/backtest_runner.py`**
- Thin wrapper around `backtest.engine.run_backtest()`
- Load config, fetch data via `mcp_client`, call engine, print report
- Replace `scripts/run_backtest.py`

**Step 1.4 — Update `scripts/run_live.py`**
- Entry point: `python scripts/run_live.py [--dry-run] [--symbol EURUSD]`
- Load config + secrets
- Instantiate `LiveRunner` and run

### Phase 2: API & Dashboard

**Step 2.1 — Move and clean `dashboard_api.py` → `api/dashboard_api.py`**
- Remove import from `orchestrator.py`
- Import from `mcp_client`, `journal/store`, `config`
- Add `/api/version` endpoint
- Add `/api/health` endpoint
- Keep WebSocket on `/ws`

**Step 2.2 — Create `api/schemas.py`**
- Pydantic models for all API responses
- `Bar`, `AccountSnapshot`, `Position`, `TradeRecord`, `DigestResponse`

**Step 2.3 — Create vanilla JS dashboard**
- Convert `trading-cockpit.jsx` to plain HTML/CSS/JS
- Remove React dependency (no build step required)
- Use vanilla DOM APIs + Chart.js via CDN
- Connect to `http://localhost:8158/api/*` and `ws://localhost:8158/ws`
- Display version from `/api/version`
- Remove all mock data generators

### Phase 3: Documentation & Polish

**Step 3.1 — Update `README.md`**
- Remove references to `orchestrator.py`, `run_live_demo.py`, `run_journal_review.py`
- Add new commands: `python scripts/run_live.py`, `python scripts/run_backtest.py`
- Document ports: dashboard `:8058`, API `:8158`
- Add version badge

**Step 3.2 — Update `ARCHITECTURE.md`**
- Reflect new file structure
- Remove Ollama predictor/reflector from main loop
- Document the optional journal digest via Anthropic API as a separate offline script

**Step 3.3 — Update `pyproject.toml`**
- Add version: `0.1.0`
- Add `fastapi`, `uvicorn`, `websockets`, `anthropic` (optional) to dependencies
- Remove `ollama` from required deps (move to optional)

**Step 3.4 — Update `.gitignore`**
- Add `__pycache__/`, `*.pyc`, `.venv/`, `venv/`, `*.egg-info/`, `.env`, `*.db`, `*.db-journal`, `.DS_Store`, `data/cache/*` (keep `.gitkeep`), `reports/*` (keep `.gitkeep`)

**Step 3.5 — Add version to UI**
- Read version from `package.json` equivalent or API `/api/version`
- Display in dashboard footer

---

## 5. Testing Strategy

### 5.1 Existing Tests (Preserve)
All existing tests in `tests/` must continue to pass:
- `test_levels.py`
- `test_regime.py`
- `test_signals.py`
- `test_volume_profile.py`
- `test_risk_manager.py`
- `test_backtest_engine.py`

### 5.2 New Tests Required

| Test File | Coverage |
|---|---|
| `test_journal_store.py` | CRUD operations, digest retrieval, trade counting |
| `test_live_runner.py` | Cycle execution with mocked MCP, risk gate rejection, dry-run mode |

### 5.3 Test Commands

```bash
pytest tests/ -v
```

---

## 6. Migration Checklist

- [x] `journal/store.py` created and tested
- [x] `execution/live_runner.py` created and tested
- [x] `execution/backtest_runner.py` created
- [x] `scripts/run_live.py` created
- [x] `api/dashboard_api.py` moved and cleaned
- [x] `api/schemas.py` created
- [x] Dashboard HTML/CSS/JS created and wired
- [x] `orchestrator.py` removed
- [x] `dashboard_api.py` (root) removed
- [x] `ai-trader-architecture.md` removed
- [x] `trading-cockpit.jsx` removed
- [x] `README.md` updated
- [x] `ARCHITECTURE.md` updated
- [x] `pyproject.toml` updated
- [x] `.gitignore` updated
- [x] All existing tests pass
- [x] New tests added and passing
- [x] Dashboard serves on port 8058
- [x] API serves on port 8158
- [x] Training mechanisms implemented (`optimizer.py`, `simulator.py`, `run_training.py`)
- [x] `tests/test_training.py` created and passing

---

## 7. Training Mechanisms (Post-Implementation)

### 7.1 Historical Parameter Optimizer
- File: `src/ctrader_bot/training/optimizer.py`
- Sweeps parameter grids against historical data using the existing backtest engine
- Outputs top-N CSV to `data/reports/param_optimization_<timestamp>.csv`
- CLI: `python scripts/run_training.py optimize --days 60 --symbol US500`

### 7.2 Simulated Trading Engine (Deep Dive)
- File: `src/ctrader_bot/training/simulator.py`
- Bar-by-bar replay with in-memory simulated positions
- Tracks entry data snapshots (ATR, ADX, regime, volume-profile levels)
- Generates failure analysis markdown report
- CLI: `python scripts/run_training.py simulate --days 60 --analyze-failures`

---

## 8. Risk Mitigation

| Risk | Mitigation |
|---|---|
| Breaking existing strategy logic | All existing tests pass before and after refactor |
| MCP tool name drift | `mcp_client.py` uses explicit tool names; `discover_mcp_tools.py` retained |
| Dashboard CORS issues | API serves from same origin or CORS is properly configured |
| LLM confusion in trading loop | Ollama removed from live path; journal digest is offline-only |
| Port conflicts | Follow `RULES_ports.md` strictly: 8058 dashboard, 8158 API |
| Training mechanisms touching live state | Both optimizer and simulator are offline-only, never import live runner |

---

## 9. Out of Scope (Future)

- Vector database for semantic journal search (port 8458)
- Background workers for async digest generation (port 8358)
- Docker Compose setup
- Authentication/authorization on API
- Real-time WebSocket streaming from cTrader MCP (not supported by MCP server)
- ML models in the live trading loop (prohibited by project design)
