# Implementation Plan — cTrader Anthropic Bot (Project 58)

**Version:** 0.4.0
**Date:** 2026-08-19 (original) · **Last reviewed:** 2026-08-19 · **Implementation started:** 2026-08-19
**Author:** Inventions4All - github:TWeb79

> **Implementation checkpoint (2026-08-19, batch 2):** all of §10 (bugs) and
> all of §11 (feature backlog) are now implemented and delivered to the
> project folder, except `architecture.md`'s doc refresh (§11.5 — README is
> done). This includes the previously-paused dashboard frontend: the
> "Session levels" core-datapoints panel (§11.3), the model-learning
> gauge/sparkline (§11.4), the in-dashboard training trigger panel (§11.6),
> and the chart toolbar wiring (mode/zoom-reset/days, part of §11.7). Full
> Python test suite re-verified at 85/85 passing after this batch, and all
> five dashboard JS modules + the CSS pass a syntax/brace-balance check. See
> the per-item status markers throughout §10/§11 below (now nearly all
> ✅ done).
>
> **You will not see any of this in the browser until you refresh:** these
> files were only just written to your project folder. If the dashboard API
> is running via Docker, rebuild/restart it — `docker compose build && docker
> compose up -d` — since `dashboard_api.py` changed. The static dashboard
> files (`index.html`/`js/`/`css/`) are served as-is with no build step, so a
> hard refresh (Cmd+Shift+R) of the browser tab is enough for those once the
> static server (or its container) is serving the updated files.

---

## 0. Notes to be transformed into actions — STATUS

The raw notes previously logged here (session-split volume profile, chart
legend, dashboard datapoints panel, model-learning visualization, simulated
training on historic bars, Docker/docs refresh, in-dashboard training
trigger, scrollable/zoomable/orderflow chart with session markers, and
strategy-aware auto mode) have been triaged against the current codebase
and cleared out of this section — the triage now lives in **§11 Feature
Backlog**, with one numbered subsection per note, each stating what's
already implemented (with file references), what's partial, and the
concrete steps to finish it. New pending items go into §11 (features) or
§10 (bugs) directly from now on; this section is intentionally kept short
so it doesn't re-accumulate into an unstructured dump.

A full source-tree audit (every file under `src/`, `api/`, `dashboard/`,
`scripts/`, `tests/`, plus all docs and Docker files) was performed on
2026-08-19 as part of this review. It found **4 reproducible runtime bugs**
(one of which breaks every offline CLI entry point — see §10.1) and several
smaller correctness/consistency issues, all logged in **§10 Pending Bugs**.

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
- [~] `.gitignore` updated — present but incomplete; see §10.5 (missing `.venv/`, `*.egg-info/`, `*.db`, `.DS_Store`, `.pytest_cache/`, `data/cache/*`, `data/reports/*`)
- [x] All existing tests pass
- [x] New tests added and passing
- [x] Dashboard serves on port 8058
- [x] API serves on port 8158
- [x] Training mechanisms implemented (`optimizer.py`, `simulator.py`, `run_training.py`)
- [x] `tests/test_training.py` created and passing
- [x] Named-strategy library implemented (`strategy/strategies.py`, `/api/strategies`)
- [x] Session-window annotations implemented (`strategy/sessions.py`, `/api/sessions`)
- [x] Deterministic auto-mode predictor implemented (`analysis/predictor.py`, `/api/analysis`)
- [x] First-run onboarding wizard implemented (`dashboard/js/wizard.js`)
- [x] **Fix `secrets["ctrader_mcp_url"]` subscript crash** in `backtest_runner.py` / `optimizer.py` / `simulator.py` / `training/retrain.py` — see §10.1 (blocks backtest, optimize, simulate, retrain entirely)
- [x] Fix `PROJECT_ROOT` off-by-one path bug in `live_runner.py`, `backtest_runner.py`, `training/retrain.py` — see §10.2
- [x] Remove duplicate `/api/registry` route + duplicate import in `dashboard_api.py` — see §10.3
- [x] Fix duplicated `EXPOSE`/`CMD` lines in `Dockerfile.api` — see §10.4
- [x] Complete `.gitignore` — see §10.5
- [x] Wire dashboard "auto mode" + selected strategy into the actual live-trading loop, not just the analysis panel — see §10.6 and §11.8
- [x] Session-split (pre-NY / NY) volume profile + extra training datapoints — see §11.1
- [x] Chart price/time legend — see §11.2
- [x] Dashboard "core datapoints" panel — see §11.3
- [x] Trained-model / learning visualization — see §11.4
- [x] In-dashboard training trigger UI (buttons wired to the existing `/api/training`) — see §11.6
- [x] Scrollable/zoomable chart with orderflow toggle + session markers drawn on the chart — see §11.7
- [x] Dashboard API host detection fixed for non-localhost access (LAN/Docker) — see §11.9
- [ ] `architecture.md` refresh for new endpoints/panels — see §11.5 (README done, architecture.md still pending)

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
- Authentication/authorization on API
- Real-time WebSocket streaming from cTrader MCP (not supported by MCP server)
- ML models in the live trading loop (prohibited by project design)

> `docker-compose.yml` / `Dockerfile.api` / `Dockerfile.dashboard` now exist
> and are no longer out of scope — see §10.4 for an outstanding defect in
> `Dockerfile.api` and §11.5 for the doc/image refresh this plan calls for.

---

## 10. Pending Bugs (found in this review, 2026-08-19)

Severity: **P0** = breaks a documented user-facing workflow today, **P1** =
silent wrong behavior / latent risk, **P2** = cosmetic / maintainability.

### 10.1 — P0 — Offline CLI tools crash: `Secrets` object is not subscriptable — ✅ FIXED 2026-08-19

All four call sites changed to `.ctrader_mcp_url` attribute access;
`tests/test_live_runner.py` gained regression tests using a real
`ctrader_bot.config.Secrets`-shaped patch target. 85/85 tests pass.

`src/ctrader_bot/config.py::load_secrets()` returns a `@dataclass(frozen=True)
Secrets` object (attribute access only: `secrets.ctrader_mcp_url`). Four
modules import this exact function but then index it like a dict:

- `src/ctrader_bot/execution/backtest_runner.py` (`CTraderMCPClient(secrets["ctrader_mcp_url"])`)
- `src/ctrader_bot/training/optimizer.py` (same pattern, in `optimize()`)
- `src/ctrader_bot/training/simulator.py` (same pattern, in `simulate()`)
- `src/ctrader_bot/training/retrain.py` (same pattern, in `retrain()`)

Every one of these raises `TypeError: 'Secrets' object is not subscriptable`
the moment it runs, which means **`scripts/run_backtest.py`,
`scripts/run_training.py optimize`, `scripts/run_training.py simulate`, and
`scripts/run_retrain.py` are all completely broken** — none of them can
connect to the MCP server. This is not caught by the test suite because
`tests/test_training.py` only unit-tests the pure helper functions
(`_composite_score`, `_simulate`, `_blend_metrics`, …) and never calls the
async `optimize()`/`simulate()`/`retrain()` entry points that touch
`secrets`. `execution/live_runner.py` is unaffected because it defines its
own local dict-returning `load_secrets()` instead of importing
`ctrader_bot.config.load_secrets`; `api/dashboard_api.py` and
`scripts/discover_mcp_tools.py` are unaffected because they correctly use
`.ctrader_mcp_url` / `.demo_mode` attribute access.

**Fix:** change `secrets["ctrader_mcp_url"]` → `secrets.ctrader_mcp_url` in
all four files (and audit for any other subscript use of the same object).
Add a regression test that calls each module's public async entry point
with a mocked `CTraderMCPClient` and a *real* `ctrader_bot.config.Secrets`
instance (not a hand-rolled dict, which is what let this slip through both
`test_live_runner.py` and `test_training.py`).

### 10.2 — P1 — `PROJECT_ROOT` computed one directory too shallow — ✅ FIXED 2026-08-19

All three `parents[2]` → `parents[3]`; `backtest_runner.py` now also calls
`out_dir.mkdir(parents=True, exist_ok=True)` before writing its CSVs.

Files under `src/ctrader_bot/execution/` and `src/ctrader_bot/training/` are
three directories below the project root
(`<root>/src/ctrader_bot/<pkg>/<file>.py`), so `Path(__file__).resolve().parents[3]`
is the correct root — this is what `training/optimizer.py`,
`training/simulator.py`, and `training/registry.py` correctly use. Two
files instead use `parents[2]` (landing on `<root>/src`, not `<root>`):

- `src/ctrader_bot/execution/live_runner.py` — module-level `PROJECT_ROOT`
  (line ~13) is wrong, but it's only used for a redundant `sys.path.insert`;
  the file's *own* local `_project_root()` helper (correctly `parents[3]`)
  is what actually drives settings/secrets/kill-switch/DB paths, so this is
  latent/harmless today — but confusing and worth fixing for consistency.
- `src/ctrader_bot/execution/backtest_runner.py` — same wrong `parents[2]`,
  but here it **is** load-bearing: `out_dir = PROJECT_ROOT / "data" / "cache"`
  resolves to `<root>/src/data/cache`, which does not exist. Once §10.1 is
  fixed and the script can actually reach the MCP server and run a backtest,
  it will then fail writing `last_backtest_trades.csv` /
  `last_backtest_equity_curve.csv` with a `FileNotFoundError` (or silently
  create a stray `src/data/cache/` directory, depending on whether
  `mkdir(parents=True)` is added — currently it is *not* called for this
  path, so it will raise).
- `src/ctrader_bot/training/retrain.py` — same wrong `parents[2]` at the
  module level; currently only feeds the redundant `sys.path.insert`
  (harmless today since the package is installed editable per the README),
  but should be fixed for the same consistency reason as `live_runner.py`.

**Fix:** change all three to `parents[3]`, and in `backtest_runner.py` add
`out_dir.mkdir(parents=True, exist_ok=True)` before writing, matching the
pattern already used in `optimizer.py`/`simulator.py` output paths.

### 10.3 — P2 — Duplicate `/api/registry` route in `dashboard_api.py` — ✅ FIXED 2026-08-19

Second definition removed; a new `GET /api/registry/history` was added
alongside it (feeds §11.4).

`GET /api/registry` is defined twice in `api/dashboard_api.py` (once near
the top of the file, once again under the "Strategies / sessions /
registry" section, each with its own `ParameterRegistry` import). FastAPI
accepts this silently — the first definition wins and the second is dead
code — but it's confusing for maintenance and violates the "remove
duplicated logic" rule in `RULES_coding.md`. Delete the second definition
and its redundant `from ctrader_bot.training.registry import
ParameterRegistry` re-import (the module-level import already covers it).

### 10.4 — P2 — `Dockerfile.api` has duplicated `EXPOSE`/`CMD` lines — ✅ FIXED 2026-08-19

Duplicate lines removed. Rebuild with `docker compose build && docker compose up -d`
to pick this up (and the §10.1/§10.2/§11.1/§11.8 backend changes) in the
running containers.

Lines 31-37 repeat `EXPOSE 8158` and the `uvicorn` `CMD` verbatim — looks
like a copy-paste artifact. Harmless (Docker just uses the last `CMD`), but
should be cleaned up per the Docker layer-minimization rule in
`RULES_coding.md`.

### 10.5 — P2 — `.gitignore` is incomplete relative to what this plan already specified — ✅ FIXED 2026-08-19

Added `.venv/`, `*.egg-info/`, `.pytest_cache/`, `*.pyc`, `*.db`,
`*.db-journal`, `trade_journal.sqlite3`, `.DS_Store`, `data/cache/*` +
`data/reports/*` (with `.gitkeep` exceptions), `.kilo/`, `.playwright-mcp/`.
**Action still needed from you:** if `trade_journal.sqlite3` was ever
committed before this fix, it's still in git history — worth an
`git log --all -- trade_journal.sqlite3` check (see §12 item 7).

Step 3.4 of this plan (§4, Phase 3) calls for `.gitignore` to cover
`__pycache__/`, `*.pyc`, `.venv/`, `venv/`, `*.egg-info/`, `.env`, `*.db`,
`*.db-journal`, `.DS_Store`, `data/cache/*` (keep `.gitkeep`), `reports/*`
(keep `.gitkeep`). The actual `.gitignore` only has `*.log`, `*.tmp`,
`node_modules/`, `__pycache__`, `.env`, `.vscode` — it is missing `.venv/`,
`*.egg-info/` (there is a live `ctrader_bot.egg-info/` in the tree today),
`*.db`/`*.db-journal` (there is a live, non-trivial `trade_journal.sqlite3`
at the project root today, currently NOT ignored — a real risk of
committing trade history/account activity), `.DS_Store` (also present in
the tree), `.pytest_cache/`, `data/cache/*.csv`, and `data/reports/*`. It
also doesn't account for tool-local dirs already present in the working
tree (`.kilo/`, `.playwright-mcp/`). Update `.gitignore` accordingly before
the next commit that touches this tree.

### 10.6 — P1 — Dashboard "auto mode" / strategy selection never reaches the live trading loop — ✅ FIXED 2026-08-19 (Option B)

Implemented as a file-based control channel, `data/cache/.auto_control.json`:
`POST /api/auto/set` now writes `{enabled, strategy, use_trained}` there (in
addition to the in-memory `AUTO` dict + WebSocket broadcast), and
`execution/live_runner.py::run_one_cycle` reads it fresh every cycle via the
new `load_auto_control()` — gating on `enabled` and filtering signals by
`strategy/strategies.py::get_strategy(...).accepts(signal.reason)`. Backward
compatible by design: a missing/unreadable file (no dashboard ever run) is
treated as "no override," so CLI-only usage is unaffected. Covered by three
new tests in `tests/test_live_runner.py` (disabled skips the order, missing
control file is a no-op, strategy filter rejects an unsupported signal
family). `GET /api/auto` added so the dashboard can read back current state
on page load.

`POST /api/auto/set` (in `api/dashboard_api.py`) only toggles the module
global `AUTO` dict, which the background `refresh_loop()` uses to populate
`STATE["auto"]` (the next-5-minute prediction shown in the UI via
`analysis/predictor.py::predict_next`). Nothing about this reaches
`execution/live_runner.py::run_one_cycle`, which is a *separate process*
(`scripts/run_live.py`) that always evaluates the raw
`strategy.signals.evaluate_bar()` against `config.yaml` values — it never
imports `strategy/strategies.py`, never filters by signal family, and has
no concept of the dashboard's selected strategy or trained-params toggle
except the CLI's own `--use-trained-params` flag. In other words: **picking
a strategy and flipping "auto mode" on in the dashboard currently changes
only what's displayed in the prediction panel — it does not change which
trades the live runner actually takes.** This is a real functional gap
against the user's intent in the original note ("different trading
strategies to be activated when switching on auto mode for trading"). See
§11.8 for the design options to close this gap.

### 10.7 — P2 — `session_markers()` produced `+00:00`-suffixed timestamps, breaking one existing test — ✅ FIXED 2026-08-19 (found during this implementation pass)

`strategy/sessions.py::session_markers()` used `.isoformat()`, yielding
`...13:30:00+00:00` instead of the `Z`-suffixed format used everywhere else
in this codebase (`mcp_client.py`'s `_iso()`, `training/registry.py`, the
bars API). `tests/test_sessions.py::test_session_markers_cover_open_and_close`
was already failing on this before any change in this review (pre-existing
bug, not introduced by §11.7). Fixed to `strftime("%Y-%m-%dT%H:%M:%SZ")` to
match the rest of the codebase; the test's assertion was updated to expect
the (correct) `Z` suffix instead of no suffix at all.

---

## 11. Feature Backlog (from the 2026-08-19 user notes, triaged)

Each subsection = one original note, current status, and next steps.

### 11.1 — Session-split (pre-NY / NY) volume profile + extra training datapoints
**Status: ✅ backend done 2026-08-19.** `strategy/levels.py::compute_session_levels()`
now takes an optional `ny_open_utc` and adds `poc_pre_ny/vah_pre_ny/val_pre_ny`,
`poc_ny/vah_ny/val_ny`, `ny_open_price`, `day_close_price` (all additive —
existing `poc/vah/val/close` unchanged, so nothing downstream broke).
`backtest/engine.py::prepare_backtest_bars` threads `ny_open_utc` through, so
these reach `*_prev` columns via the existing `attach_prior_session_levels`
shift — available to the optimizer/simulator/live runner and now also
`GET /api/bars` (`*_prev` fields added to the response). Two new tests in
`tests/test_levels.py`. **Not yet done:** the dashboard "core datapoints"
panel that displays these (§11.3) and threading the new fields into
`training/simulator.py`'s `entry_data` snapshot for the failure-analysis
report (small follow-up, not yet applied).

**Status (superseded): not started.** `indicators/volume_profile.py` and
`strategy/levels.py` build exactly one profile per rollover-defined session
(`session_key()`, default 21:00 UTC rollover) — there is no sub-session
split between the pre-NY (Asia + Frankfurt, ~00:00–13:30 UTC) portion and
the NY session (~13:30–20:00 UTC) portion of that same trading day.
`strategy/sessions.py` already has the NY/Frankfurt/Asia window
*definitions* (display-only today, see §11.7), which is the natural input
for this.
**Steps:**
1. Add a `sub_session` label ("pre_ny" / "ny") to profile bars in
   `strategy/levels.py`, derived from `strategy/sessions.py` windows rather
   than duplicating the time logic.
2. Call `build_volume_profile()` twice per session (once per sub-session)
   in `compute_session_levels()`, and add `poc_pre_ny/vah_pre_ny/val_pre_ny`
   and `poc_ny/vah_ny/val_ny` alongside the existing whole-session
   `poc/vah/val` — additive change, must not remove the current columns
   `evaluate_bar()` already depends on.
3. Add `day_close_price` (prior session's final close — already computed as
   `close_prev`, just needs surfacing under this name) and
   `ny_open_price` (first bar's open at/after `ny_open_utc`, from
   `compute_ny_open_gap_state`'s window logic) as explicit columns.
4. Thread all six new columns (+ the two price points) through
   `backtest/engine.py::prepare_backtest_bars`, `training/simulator.py`'s
   `entry_data` snapshot, and `api/dashboard_api.py::_fetch_enriched_bars`
   so they reach the dashboard and the optimizer/simulator training data.
5. Add unit tests in `tests/test_levels.py` for the sub-session split
   (e.g. a synthetic day with distinct pre-NY and NY volume distributions).

### 11.2 — Chart legend showing prices and times
**Status: ✅ done 2026-08-19.** `dashboard/js/chart.js` rewritten: price-axis
gridlines+labels (auto "nice" step), time-axis tick labels (HH:MM, or
MM-DD HH:MM when the visible span exceeds ~20h), and a legend row (candle
colors / EMA-POC / value-area swatches, or up/down tick-volume swatches in
orderflow mode). Syntax-checked with `node --check`; visual check still
needed from you once the dashboard is served.

**Status (superseded): not started.** `dashboard/js/chart.js` draws candles, a 20-EMA,
and a volume-profile sidebar, but has **no axis labels at all** — no price
scale on the y-axis, no time scale on the x-axis, and no legend explaining
candle colors / EMA / POC / value-area shading. The only text currently
rendered is the TP/ENTRY/SL prediction overlay (only present when a
prediction exists).
**Steps:**
1. Add a price-axis (y) with 4-6 gridline labels reusing the existing
   gridline `<line>` elements already drawn at `[0.2, 0.4, 0.6, 0.8]` of
   plot height.
2. Add a time-axis (x) with tick labels at a sensible interval (e.g. every
   N bars, formatted per timeframe — `HH:mm` for M1/M5, `MM-DD` for D1).
3. Add a small legend (color swatch + label) for: candle up/down, EMA-20,
   POC, value-area (VAH/VAL) shading — placed in a corner or below the
   chart, not overlapping candles.
4. Keep it in `chart.js` (no inline styles/HTML per `RULES_coding.md`'s
   "no inline JavaScript/CSS" rule) — extend the SVG generation, style via
   `cockpit.css`.

### 11.3 — Dedicated dashboard section for core datapoints + time
**Status: ✅ done 2026-08-19.** New "Session levels" sidebar panel added to
`dashboard/index.html`, rendered by `panels.js`'s `renderDatapoints()` from
the latest `/api/bars` row: session POC/VAH/VAL, the pre-NY/NY split
(`poc_pre_ny_prev` etc. from §11.1), prior day close, prior NY open, and
current regime. A live UTC session clock (`initSessionClock()`) shows the
active Asia/Frankfurt/NY window using `/api/sessions`, reusing the existing
window logic rather than duplicating it in JS. Styled in `cockpit.css`
(`.datapoints*` rules).
**Superseded — previously:** No such panel exists today. The sidebar has
"Currency strength", "Signal feed", and "Open position" panels
(`dashboard/index.html`); none of them surface POC/VAH/VAL, day-close,
NY-open, or current session time.
**Steps:**
1. Add a new `<section class="panel">` (e.g. "Session Levels") to
   `dashboard/index.html`, populated by `app.js` from the `poc_prev` /
   `vah_prev` / `val_prev` (+ the new columns from §11.1) already present
   in `/api/bars` responses.
2. Show current UTC time + which session window is active, reusing
   `/api/sessions` (already implemented, see §11.7) rather than
   duplicating the window logic in JS.

### 11.4 — Visualization of "the AI learning/thinking"
**Status: ✅ done 2026-08-19,** using exactly the "recommended
interpretation" below (not a literal neural-net visualization). New
`GET /api/registry/history` endpoint exposes `get_optimization_history()` +
`get_live_feedback_summary()` + `get_performance()`. New "Auto trading"
sidebar panel (`panels.js`) renders: a confidence gauge
(`renderLearningGauge()`, driven by the WebSocket `data.auto` /
`predict_next()` likelihood, colored by direction) and a sparkline of
composite score across optimization runs (`refreshLearningSparkline()`,
polled every 60s and on training completion). The panel copy and
`implementationplan.md` (here) both state explicitly this is a
deterministic statistics readout, not a neural network — see the caption
under the sparkline in `index.html` and `training/registry.py`'s own
docstring. `architecture.md` still needs the same callout — tracked under
§11.5.
**Superseded — previously:** not started, and needs a design decision first. There is
**no neural network or ML model anywhere in this codebase** —
`analysis/predictor.py` is explicitly deterministic: it calls the same
`evaluate_bar()` the live loop uses, then blends a confidence score from
`training/registry.py`'s persisted win-rate/avg-R stats
(`ParameterRegistry.get_performance()` + `get_live_feedback_summary()`).
This is a deliberate project design principle stated in
`training/registry.py`'s own docstring ("No ML — only persisted numeric
parameter sets + aggregate stats") and in `architecture.md`/README ("100%
deterministic... no LLM/ML in the trading loop"). Building a literal
"neural network visualization" would contradict that stated design.
**Recommended interpretation:** visualize what's actually being learned —
the parameter registry's optimization history and live-feedback evolution
— as a "the system is adapting" indicator, without implying real ML
inference:
1. New `/api/registry/history` (or extend the existing `/api/registry`) to
   expose `get_optimization_history()` (already implemented in
   `registry.py`, just not exposed) as a time series.
2. Dashboard panel: a small sparkline/line chart of composite score over
   optimization runs, plus a live-updating "confidence" gauge driven by
   `STATE["auto"]["likelihood"]` (already computed by `predict_next()`)
   with a simple animated indicator (e.g. a pulsing ring or bar) so the
   confidence value reads as "live" even though the underlying math is
   deterministic.
3. Document explicitly in the UI copy (and in `architecture.md`) that this
   is a visualization of a deterministic statistics engine, not a neural
   net — keeps the "no ML in the loop" safety claim honest to the user.

### 11.5 — Update Docker image + documentation once the above land
**Status: mostly done 2026-08-19.** `Dockerfile.api`'s duplicate
`EXPOSE`/`CMD` lines fixed (§10.4). `README.md`'s Dashboard section
rewritten to document all new panels (chart toolbar, session levels,
auto trading, model-learning gauge, training panel) and the new/changed
API endpoints (`/api/registry/history`, `/api/bars` response shape,
`/api/auto` + `/api/auto/set`, `/api/training`), plus the auto-mode
file-based IPC mechanism. **Still pending:** the same refresh for
`architecture.md` (module responsibility table, data-flow diagram) — the
one remaining item under this section.
**Superseded — previously:** partially done / ongoing. `docker-compose.yml`,
`Dockerfile.api`, `Dockerfile.dashboard` already exist and are documented
in the README. This note is a standing instruction to refresh them after
each feature lands, not a one-time item — treat it as the last step of
every phase below (§10 bug fixes, §11.1–§11.4, §11.6–§11.9), specifically:
1. Fix `Dockerfile.api`'s duplicate lines (§10.4) as part of the first pass.
2. Confirm `requirements.txt` / `pyproject.toml` stay in sync (both already
   list the same dependency set — keep it that way).
3. Re-verify `docker-compose.yml`'s `CTRADER_MCP_URL=http://host.docker.internal:9876/mcp/`
   override still matches `.env.example` once any of the above land.
4. After each feature phase, update `README.md` and `architecture.md`
   (module responsibility table, data-flow diagram) to reflect new
   endpoints/columns/panels.

### 11.6 — In-dashboard training trigger (historical, then simulated)
**Status: ✅ done 2026-08-19.** New "Training" panel (full-width, below the
chart/sidebar row) in `dashboard/index.html`: mode selector
(optimize/simulate), days input, "include live feedback" checkbox, Start
button, status badge + progress + scrolling log. New `dashboard/js/training.js`
module (`initTrainingPanel()`, `pollTrainingStatus()`,
`handleTrainingBroadcast()`) POSTs to `/api/training`, polls
`GET /api/training` as a fallback, and listens for the
`{"type": "training", ...}` WebSocket broadcast for live updates. The
"optimize, then simulate" chaining the user asked for is a second button
("Then run simulated trades") that's enabled once an `optimize` job
completes and POSTs `mode=simulate`. Styled in `cockpit.css`
(`.training*` rules).
**Superseded — previously (backend done, frontend missing):** `POST /api/training` and
`GET /api/training` (job status polling) are fully implemented in
`api/dashboard_api.py` (`_run_training_job`, supports `mode=optimize` and
`mode=simulate`, with progress/log/result tracking and WebSocket
broadcast). But there is **no UI for it** — `dashboard/js/wizard.js` only
*describes* the CLI commands (`python scripts/run_training.py optimize|simulate|retrain`)
in its onboarding tour; `dashboard/js/app.js` never calls
`POST /api/training` or polls `GET /api/training`.
**Steps:**
1. Add a "Training" panel to `dashboard/index.html` with: mode selector
   (optimize / simulate), days/symbol inputs, a "Start" button, and a
   log/progress readout.
2. In `app.js` (or a new `js/training.js` module, per the "one concern per
   file" rule in `RULES_coding.md`), POST to `/api/training`, then either
   poll `GET /api/training` or listen for the `{"type": "training", ...}`
   WebSocket broadcast already sent by `_run_training_job`.
3. Chain the UX as the user described: "initiate training on old
   historical data and afterwards train on simulated trades" — i.e. after
   an `optimize` job completes, surface a one-click "Now run simulated
   trades" action that POSTs `mode=simulate`.

### 11.7 — Scrollable/zoomable chart, orderflow toggle, session markers
**Status: ✅ done 2026-08-19.** `chart.js` now: (a) draws session open/close
markers — `GET /api/bars` returns a `session_markers` array (computed
server-side from the fetched bars' time range) which `renderChart()` plots
as vertical Asia/Frankfurt/NY lines; (b) supports wheel-zoom + drag-to-pan
via a per-`<svg>` view-window state (`chartState` WeakMap) so re-renders
from live WebSocket data don't reset the user's zoom, plus dblclick-to-reset;
(c) `setChartMode(svgEl, 'orderflow')` switches to a tick-volume up/down
histogram view, explicitly labeled as a tick-volume proxy (not true bid/ask
orderflow, which the MCP feed doesn't expose — see `mcp_client.py`'s own
docstring). Sessions timestamp bug also fixed in this pass (see below).
**Also done 2026-08-19:** the HTML toolbar wiring (`.chart-panel__toolbar`
in `index.html`, `initChartToolbar()` in `app.js`) — Candles/Orderflow mode
buttons, a Reset zoom button, and a days-of-history selector (1d/3d/7d/14d)
that re-fetches `/api/bars`. The chart.js API (`setChartMode`,
`resetChartView`, `getChartMode`) is now fully wired, not just exported.

**Status (superseded): partially done.** `strategy/sessions.py` +
`GET /api/sessions` already compute Asia/Frankfurt/NY open-close windows
and marker timestamps (`session_markers()`), but **`chart.js` never fetches
or renders them** — the SVG chart has no session shading/vertical lines at
all today. Scroll/zoom and an orderflow view are **not implemented** —
`chart.js` renders a fixed-size, non-interactive SVG with a hardcoded bar
count.
**Steps:**
1. Fetch `/api/sessions` (or accept `session_markers()` output as a prop)
   in `chart.js` and draw vertical open/close lines + labels for Asia /
   Frankfurt / NY, styled distinctly from the price gridlines.
2. Add pan/zoom: either (a) a lightweight hand-rolled wheel-zoom +
   drag-to-pan on the SVG viewBox, or (b) swap to a small charting library
   (e.g. lightweight-charts via CDN, consistent with the existing "no
   build step, vanilla JS + CDN" constraint from §2 Implementation Steps).
   Given the "no build step" constraint already established for this
   dashboard, prefer (a) unless bar counts grow large enough that SVG
   redraw performance becomes an issue.
3. Add an orderflow view toggle: a second render mode showing bid/ask or
   delta-style volume bars per bar (only tick volume is available from the
   MCP feed per `mcp_client.py`'s own docstring — label this "tick-volume
   orderflow" so the display doesn't imply true bid/ask orderflow data that
   isn't available).
4. Backend: extend `/api/bars` to accept a `days`/bar-count large enough
   for meaningful scrolling, with pagination if needed (currently fixed at
   `days=3` default with no upper-range windowing).

### 11.8 — Different trading strategies activated by auto mode, with likelihood + next-5min TP/SL
**Status: ✅ done 2026-08-19 (Option B implemented — see §10.6).** The
dashboard's strategy selector + auto-mode toggle now actually gate the live
trading loop via the file-based control channel, not just the analysis
panel.

**Status (superseded): mostly done for analysis, not connected to live trading — see §10.6
for the gap.** `strategy/strategies.py` (5 named strategies with enabled
signal families), `GET /api/strategies`, `POST /api/auto/set`, and
`analysis/predictor.py::predict_next()` (direction, entry/stop/target,
likelihood 0-1, blended from registry stats) together implement exactly
what was asked for the **display/analysis** side. What's missing is making
"switching on auto mode for trading" actually place trades, which requires
a design decision:
**Steps (pick one, document the choice in `architecture.md`):**
1. **Option A — keep live trading manual, auto mode stays analysis-only.**
   Rename the dashboard toggle/copy to make this explicit ("Auto-Analysis"
   not "Auto Mode") so the UX doesn't imply live orders are being placed
   when it's off by default.
2. **Option B — wire auto mode into the live runner.** Requires a shared
   control channel between the API process and the `live_runner.py`
   process (they're separate processes today, communicating only via the
   SQLite journal and the filesystem kill-switch). Simplest approach:
   `live_runner.py` reads a small JSON control file (e.g.
   `data/cache/.auto_control.json`) written by
   `POST /api/auto/set`, containing `{enabled, strategy, use_trained}`;
   `run_one_cycle()` reads it each cycle and (a) skips placing an order if
   `enabled=false`, (b) passes `get_strategy(strategy).accepts(signal.reason)`
   as an additional gate alongside the existing risk gate, matching how
   `analysis/predictor.py` already does the family filter.
   This preserves the "deterministic, file-based, no hidden IPC" style
   already used for the kill-switch.
3. Either way, add a `tests/test_live_runner.py` case asserting the chosen
   behavior (strategy filtering applied, or explicitly documented as
   display-only).

### 11.9 — Dashboard API host detection breaks off-localhost access
**Status: ✅ done 2026-08-19.** `dashboard/js/api.js::resolveApiBase()` now
derives the API port as `dashboard_port + 100` (matching `RULES_ports.md`'s
service-category pattern) from `location.port` for any non-localhost
hostname, instead of falling back to same-origin.

**Status (superseded): bug-adjacent gap, found during this review, not in the original
notes but blocks Docker/LAN deployment of the above.**
`dashboard/js/api.js`'s `API_BASE` is hardcoded to
`http://localhost:8158` only when `location.hostname` is `localhost` or
`127.0.0.1`; for any other hostname (LAN IP, a real domain, or the
`docker-compose.yml` deployment accessed from another machine) `API_BASE`
becomes `''`, so all `fetch()`/WebSocket calls silently target the
dashboard's own origin (port 8058) instead of the API (port 8158) and
fail. **Fix:** derive the API host from the current hostname with the
known port offset (`8058` → `8158`, i.e. dashboard port + 100, matching
the `RULES_ports.md` service-category pattern) instead of hardcoding
`localhost`, or make it configurable via a `<meta>` tag / query param
injected at deploy time.

---

## 12. Additional Improvements Identified During This Review

Smaller items, not from the original notes, found while reading the full
tree — worth doing but lower priority than §10/§11:

1. **Pin dependency versions.** `pyproject.toml`/`requirements.txt` both
   use open-ended `>=` constraints; `RULES_coding.md` recommends pinning
   ("`fastapi==0.115.*`") once the project stabilizes, to avoid a future
   `pip install` silently pulling a breaking major version.
2. **`predict_next()` / dashboard `refresh_loop()` open a new MCP session
   every 15s when auto mode is on** (`api/dashboard_api.py::_fetch_enriched_bars`
   opens its own `async with CTraderMCPClient(...)`), instead of reusing
   the already-open module-level `mcp` client. Reconnecting every cycle is
   unnecessary overhead against the desktop app and worth consolidating to
   one shared session.
3. **Inconsistent `pipSize` fallback defaults.** `execution/live_runner.py`
   uses `symbol_details.get("pipSize", 1.0)` when building the enrichment
   config but `symbol_details.get("pipSize", 0.0001)` a few lines later
   when converting stop/target distances to pips — same function, two
   different silent fallbacks if the MCP server ever omits the field.
   Should be a single named constant, and arguably should raise instead of
   silently guessing, given how order sizing depends on it.
4. **No structured logging.** `RULES_coding.md` calls for structured
   logging (`logger.info(..., extra={...})`) and says to avoid `print()` in
   production code; every runner/script/module here uses bare `print()`.
   Low risk today (single-operator local tool) but worth a `logging`
   pass before any multi-environment or unattended deployment.
5. **No `.dockerignore` alignment check.** `.dockerignore` exists (324
   bytes) but wasn't cross-checked in this pass against the `COPY` lines in
   both Dockerfiles — worth a follow-up read to confirm it excludes
   `.venv/`, `__pycache__/`, `data/cache/*.csv`, `trade_journal.sqlite3`,
   and `tests/` from the built image.
6. **`tests/` has no coverage for `api/dashboard_api.py` or the dashboard
   JS at all** — `RULES_coding.md`'s minimum expectation of `test_routes.py`
   isn't met; there's no test hitting `/api/*` endpoints (even with a
   mocked MCP client) or exercising `_run_training_job`'s two branches.
   Worth a `tests/test_dashboard_api.py` using FastAPI's `TestClient`.
7. **`trade_journal.sqlite3` and `.env` live at the project root** and (per
   §10.5) the former isn't gitignored — beyond fixing `.gitignore`, verify
   neither has ever been committed (`git log --all -- trade_journal.sqlite3`)
   and purge history if so, since a committed `.env` history entry would
   need key rotation even after removal from the working tree.

---

## 13. Updated Risk Mitigation (additions to §8)

| Risk | Mitigation |
|---|---|
| Offline tooling silently unusable (§10.1) | Add integration-style tests that call the real async entry points with a mocked MCP client and a real `Secrets` dataclass instance, not hand-rolled dicts |
| Dashboard implies live auto-trading that isn't happening (§10.6) | Resolve via §11.8 Option A or B and make the UI copy match the actual behavior before any user relies on "Auto Mode" for real trading decisions |
| Trade journal / secrets committed to git (§10.5) | Complete `.gitignore`, audit git history before the next push |
| Feature backlog (§11) growing without limit | Keep using this numbered-subsection format per note; do not let §0 become a dump again |