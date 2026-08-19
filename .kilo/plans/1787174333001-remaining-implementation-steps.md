# Project Audit & Remaining Implementation Steps

**Project:** 58-cTraderAnthropicBot  
**Plan Date:** 2026-08-19  
**Author:** Inventions4All - github:TWeb79

---

## 1. Current State Assessment

### 1.1 Completed (per implementationplan.md)
- `orchestrator.py` removed
- `dashboard_api.py` moved to `api/dashboard_api.py`
- `ai-trader-architecture.md` removed
- `trading-cockpit.jsx` removed
- `journal/store.py` created
- `execution/live_runner.py` created
- `execution/backtest_runner.py` created
- `scripts/run_live.py` created
- `api/schemas.py` created
- Dashboard converted to vanilla HTML/CSS/JS
- `pyproject.toml`, `requirements.txt`, `.gitignore` updated
- `data/cache/`, `data/reports/` created

### 1.2 Critical Issues Found

| # | Issue | Severity | Location |
|---|-------|----------|----------|
| 1 | `live_runner.py` uses Ollama (`get_prediction`, `get_reflection`, `get_digest`) inside the live trading loop | **Critical** | `src/ctrader_bot/execution/live_runner.py:191-219, 317-318, 362, 369` |
| 2 | Both `live_runner.py` and `dashboard_api.py` reference `SETTINGS.get("orchestrator", {})` but `config.yaml` has no `orchestrator:` section | High | `src/ctrader_bot/execution/live_runner.py:68-70`, `api/dashboard_api.py:43` |
| 3 | `live_runner.py` never calls the deterministic `strategy.signals.evaluate_bar()` | **Critical** | `src/ctrader_bot/execution/live_runner.py` |
| 4 | Legacy stub files remain outside target structure | Medium | `src/ctrader_bot/strategy/strategies.py`, `src/ctrader_bot/execution/microtrade_trainer.py`, `src/ctrader_bot/execution/pair_trader.py` |
| 5 | Dashboard still renders mock data | Medium | `dashboard/js/app.js:24-183` |
| 6 | ES module `import`/`export` used but `<script>` tags lack `type="module"` | Medium | `dashboard/index.html:87-89` |
| 7 | Missing tests | Medium | `tests/test_journal_store.py`, `tests/test_live_runner.py` |
| 8 | Missing offline journal review script | Low | `scripts/run_journal_review.py` |

### 1.3 Contradiction Callout
- README states: *"The live decision path is 100% deterministic Python — no LLM in the trading loop."*
- `architecture.md` states: *"Deterministic trading loop — no LLM in the live trading path."*
- `implementationplan.md` states: *"LLM predictor/reflector in `orchestrator.py` (skeleton, untested, contradicts '100% deterministic Python' stated in README)"*

Yet `live_runner.py` imports `ollama` and calls `get_prediction()`, `get_reflection()`, and `get_digest()` inside `run_one_cycle()`. This is the exact antipattern the docs explicitly forbid. The code must be corrected to match the documented design.

---

## 2. Design Decisions (Resolved)

### 2.1 Live Trading Path
The live runner **must** use the deterministic `strategy.signals.evaluate_bar()` + `risk.risk_manager.RiskManager` pipeline. Ollama is removed from the live loop entirely. Optional Anthropic journal digest remains offline-only in a separate script.

### 2.2 Config Cleanup
Remove `orchestrator` references. Move `bars_for_context` and `digest_every_n_trades` into `config/config.yaml` under a new `execution:` section (or reuse existing `execution:` keys where semantically correct).

### 2.3 Dashboard
Remove all mock data generators. Fix script loading with `type="module"`. Keep WebSocket + REST API integration.

---

## 3. Implementation Steps (Ordered)

### Step 1 — Fix `live_runner.py` to be truly deterministic
- Remove `import ollama`, `build_prediction_prompt`, `build_reflection_prompt`, `build_digest_prompt`, `get_prediction`, `get_reflection`, `get_digest`, `summarize_bars`, `TradeDecision`, `TradeReflection`
- Replace `run_one_cycle()` logic:
  1. Fetch signal bars + profile bars via MCP
  2. Build enriched DataFrame using `prepare_backtest_bars()` (or a live-friendly equivalent)
  3. Call `evaluate_bar()` on the latest bar
  4. If `Signal` returned, size via `RiskManager.size_trade()`
  5. Place order if approved
  6. Poll until close, record `TradeRecord` in journal (no LLM reflection)
- Keep `--dry-run`, kill-switch, crash recovery, demo-account assertion

### Step 2 — Clean up config references
- Add missing keys to `config/config.yaml` (e.g., `bars_for_context`, `digest_every_n_trades` under `execution:`)
- Update `live_runner.py` and `dashboard_api.py` to read from correct config paths

### Step 3 — Remove legacy stubs
- Delete `src/ctrader_bot/strategy/strategies.py`
- Delete `src/ctrader_bot/execution/microtrade_trainer.py`
- Delete `src/ctrader_bot/execution/pair_trader.py`

### Step 4 — Remove dashboard mock data
- In `dashboard/js/app.js`: remove `makeBreadth()`, `renderBreadth()`, `renderSignals()`, `renderPosition()`, `renderJournal()` mock calls
- Replace with real API/WebSocket data flows

### Step 5 — Fix ES module loading
- Add `type="module"` to script tags in `dashboard/index.html`

### Step 6 — Add missing tests
- `tests/test_journal_store.py` — CRUD, `get_trades`, `aggregate_stats`, `save_cycle_state`/`load_cycle_state`
- `tests/test_live_runner.py` — mocked MCP cycle, risk gate rejection, dry-run flag, kill-switch behavior

### Step 7 — Add offline journal review script
- `scripts/run_journal_review.py` — reads journal via Anthropic API, writes digest, never imported by live runner

### Step 8 — Update documentation
- Update `README.md` to reflect deterministic live runner and new commands
- Update `ARCHITECTURE.md` to remove Ollama from main loop

---

## 4. Validation

- All 37 existing tests must continue to pass
- New tests added and passing
- `python scripts/run_live.py --dry-run` starts without ImportError (ollama removed)
- `python scripts/run_live.py --help` works
- Dashboard loads without console errors and connects to API/WebSocket

---

## 5. Out of Scope
- Docker Compose setup
- Authentication/authorization on API
- Real-time WebSocket streaming from cTrader MCP (not supported by server)

---

## 6. Training Mechanisms (New)

### 6.1 Design Decision: Deterministic "Learning"
The project docs mandate a deterministic trading loop. "Training" here does **not** introduce ML models or stochastic components into the live path. Instead, it means:
- **Offline parameter optimization** using historical data
- **Simulated trading** (paper trading against history) with structured failure analysis
- **Feedback surfaces** that inform manual parameter tuning

Both mechanisms are read-only with respect to live trading. They never place real orders.

### 6.2 Training Mechanism A — Historical Parameter Optimizer

**Purpose:** Sweep strategy/risk parameter combinations against historical bars, rank by performance, surface optimal configs.

**Data inputs:** Historical signal bars + profile bars (already available via `CTraderMCPClient.get_trendbars_range`).

**Process:**
1. Define parameter grids:
   - `level_proximity_atr_mult`: [0.15, 0.25, 0.35]
   - `breakout_confirm_atr_mult`: [0.10, 0.15, 0.20]
   - `trend_direction_lookback`: [10, 20, 30]
   - `risk_per_trade_pct`: [0.5, 1.0, 1.5, 2.0]
   - `min_stop_atr_mult`: [0.3, 0.5, 0.75]
2. For each combination, run `backtest.engine.run_backtest()` with the same historical dataset.
3. Record: total return, win rate, max drawdown, Sharpe-like ratio, number of trades.
4. Surface top-N parameter sets ranked by a composite score (e.g., return / max drawdown).

**Output:** JSON/CSV report written to `data/reports/param_optimization_<timestamp>.csv`.

**Constraints:**
- Must reuse existing `prepare_backtest_bars()` and `run_backtest()` — no duplicate strategy logic.
- Must be fast: use multiprocessing or async for parallel backtest runs.
- Must never mutate `config/config.yaml` automatically; output is advisory only.

### 6.3 Training Mechanism B — Simulated Trading Engine (Deep Dive)

**Purpose:** Replay historical data bar-by-bar as if it were live, placing simulated trades in memory, then analyzing why entries failed or succeeded.

**Data inputs:** Same historical bars as backtest, but processed sequentially with explicit "entry → management → exit" lifecycle.

**Process:**
1. Load enriched bars via `prepare_backtest_bars()`.
2. Step through each bar:
   - Call `evaluate_bar()` to get a `Signal` or `None`.
   - If signal + risk allows → open simulated position (in-memory, no DB write).
   - Track: entry price, stop, target, regime, ATR, volume-profile levels at entry.
   - On next bar(s), check exit conditions (stop/target hit).
   - Record outcome: WIN/LOSS/BREAKEVEN, R-multiple, exit reason.
3. After replay, run failure analysis:
   - Group losing trades by `setup_tag`, `regime`, `entry_price` vs `poc/vah/val`, ATR at entry.
   - Surface patterns: e.g., "range_fade_vah loses 70% of the time when ADX < 18"
   - Output: structured failure report + entry-data snapshots for manual review.

**Output:** `data/reports/simulated_trades_<timestamp>.csv` + `data/reports/failure_analysis_<timestamp>.md`.

**Key difference from backtest:** The backtest engine fills trades conservatively within a single bar. The simulated engine tracks bar-by-bar state explicitly, enabling richer exit analysis (e.g., "stopped out on the very next bar after entry").

### 6.4 File Structure Additions

```
src/ctrader_bot/
├── training/
│   ├── __init__.py
│   ├── optimizer.py          # Parameter grid search using backtest engine
│   └── simulator.py          # Bar-by-bar simulated trading + failure analysis
scripts/
├── run_training.py            # CLI: python scripts/run_training.py optimize|simulate
tests/
├── test_optimizer.py          # Parameter sweep on a small synthetic dataset
├── test_simulator.py          # Simulated replay + failure analysis assertions
```

### 6.5 CLI Interface

```bash
python scripts/run_training.py optimize --days 60 --symbol US500
python scripts/run_training.py simulate --days 60 --symbol US500 --analyze-failures
```

### 6.6 Validation
- New tests pass
- Optimizer produces a CSV with expected columns
- Simulator produces trade records + failure analysis
- Neither mechanism touches live MCP or places orders
- `pytest tests/` still passes (all existing tests green)

---

## 7. Completed Steps (from previous session)
- [x] `live_runner.py` rewritten to use deterministic `evaluate_bar()` — Ollama removed
- [x] `config/config.yaml` updated with `execution.bars_for_context`
- [x] `dashboard_api.py` config reference fixed
- [x] Legacy stubs removed (`strategies.py`, `microtrade_trainer.py`, `pair_trader.py`)
- [x] Dashboard mock data removed, `type="module"` added
- [x] `tests/test_journal_store.py` created
- [x] `tests/test_live_runner.py` created
- [x] `scripts/run_journal_review.py` created
- [x] `README.md` and `ARCHITECTURE.md` updated
- [x] Training mechanisms implemented (`optimizer.py`, `simulator.py`, `run_training.py`)
- [x] `tests/test_training.py` created and passing
- [x] All 56 tests passing

---

## 8. Remaining Items (Documentation / Verification Only)

These require no source-code changes, only markdown updates or manual verification:

1. **Update `implementationplan.md` checkboxes** — Mark all items in section 6 (Migration Checklist) as `[x]` to reflect completion.
2. **Verify dashboard on port 8058** — Run `python -m http.server 8058 --directory dashboard` and confirm `http://localhost:8058` loads without console errors.
3. **Verify API on port 8158** — Run `uvicorn api.dashboard_api:app --host 0.0.0.0 --port 8158` and confirm `/api/health` and `/api/version` return JSON.
4. **Confirm no mock data in dashboard** — Open browser dev tools on the dashboard and verify no `Math.random()` or hardcoded mock arrays appear in network/logs.
5. **Update version/build timestamp** — If a new deployment is intended, bump version in `pyproject.toml` and `dashboard/index.html` footer.

---

## 10. Persistent Training State & Live Feedback Loop

### 10.1 Problem Statement
Currently, the optimizer and simulator produce one-shot CSV reports. Every restart requires re-running training from scratch. Live trade outcomes are recorded in the journal but never feed back into parameter optimization.

### 10.2 Design Decision: Deterministic Parameter Registry
Since the live loop is 100% deterministic Python (no ML models), "trained models" = **optimized parameter sets + performance statistics**. The registry persists these to disk so they survive restarts.

### 10.3 New File: `training/registry.py`

**Purpose:** Single source of truth for best parameters, performance history, and live feedback.

**Schema (JSON on disk, default `data/reports/parameter_registry.json`):**

```json
{
  "version": "0.1.0",
  "last_updated": "2026-08-19T23:00:00Z",
  "best_params": {
    "level_proximity_atr_mult": 0.25,
    "breakout_confirm_atr_mult": 0.15,
    "trend_direction_lookback": 20,
    "risk_per_trade_pct": 1.5,
    "min_stop_atr_mult": 0.5
  },
  "best_params_by_regime": {
    "RANGE": {"level_proximity_atr_mult": 0.2, ...},
    "TREND": {"level_proximity_atr_mult": 0.3, ...}
  },
  "performance": {
    "total_return_pct": 15.2,
    "max_drawdown_pct": 3.1,
    "win_rate": 0.65,
    "n_trades": 142
  },
  "live_feedback": {
    "n_live_trades": 42,
    "live_win_rate": 0.58,
    "by_setup_tag": {
      "trend_pullback_poc": {"n": 15, "avg_r": 0.8},
      "range_fade_vah": {"n": 10, "avg_r": -0.4}
    }
  },
  "optimization_history": [
    {"timestamp": "...", "composite_score": 4.2, "params": {...}, "source": "backtest"}
  ]
}
```

**API:**
- `save_best_params(params, metrics, source="backtest")` — write/update registry
- `load_best_params()` → dict — returns best params or empty dict
- `load_best_params_by_regime(regime)` → dict — returns regime-specific params or global best
- `append_live_feedback(trade_record)` — record a live trade outcome
- `get_live_feedback_summary()` → dict — aggregated live stats
- `get_optimization_history(limit=10)` → list[dict]

### 10.4 Live Runner Feedback Integration

**Change:** After a live trade closes in `run_one_cycle()`, append outcome to the registry's `live_feedback` store.

**Implementation:**
- Add `training/registry.py` import to `live_runner.py`
- After `journal.record_trade()`, call `registry.append_live_feedback(...)` with:
  - `setup_tag`, `regime`, `r_multiple`, `pnl`, `entry_price`, `atr`, `timestamp`
- This is write-only from the live runner — it never reads trained params during trading (preserves determinism and auditability).

### 10.5 Live Runner Param Loading (Opt-In)

**New CLI flag:** `--use-trained-params`

When enabled:
1. On startup, load `best_params` from registry
2. Override the corresponding keys in `settings["signals"]` and `settings["risk"]`
3. Log which parameters were overridden and their source

**Default:** `False` (config.yaml remains source of truth). This is a deliberate safety choice: operators must explicitly opt into using trained parameters.

### 10.6 Optimizer Enhancement: Include Live Feedback

**New flag:** `optimizer.py --include-live`

When enabled:
1. Load `live_feedback` from registry
2. Create synthetic "trades" from live feedback data (setup_tag, regime, r_multiple)
3. Merge with backtest results when computing composite scores
4. Optionally weight live trades higher (e.g., 2x) to favor parameters that work in current market conditions

**Alternative simpler approach:** Instead of modifying the optimizer, add a new script `scripts/run_retrain.py` that:
1. Loads best params from registry
2. Runs a focused grid search around those params (e.g., ±20% variation)
3. Updates registry if a better composite score is found
4. Includes live feedback in the scoring

### 10.7 Incremental Re-Training Script

**New file:** `scripts/run_retrain.py`

```bash
python scripts/run_retrain.py --days 30 --include-live --min-improvement 0.05
```

**Process:**
1. Load current best params from registry
2. Define a narrow grid around current best (±20% for continuous params)
3. Run backtest + optional live feedback scoring
4. If new best composite score > old score × (1 + min_improvement), update registry
5. Output diff of old vs new params

### 10.8 File Structure Additions

```
src/ctrader_bot/
├── training/
│   ├── __init__.py
│   ├── registry.py           # NEW: persistent parameter + feedback store
│   ├── optimizer.py          # UPDATED: add --include-live flag
│   └── simulator.py          # unchanged
scripts/
├── run_retrain.py            # NEW: incremental re-training around best params
tests/
├── test_registry.py          # NEW: persistence, loading, live feedback
```

### 10.9 Config Additions

Add to `config/config.yaml`:

```yaml
training:
  registry_path: "data/reports/parameter_registry.json"
  feedback_weight: 2.0          # live trades count 2x in scoring
  auto_retrain_after_trades: 20 # trigger retrain after N new live trades (0 = disabled)
  min_improvement_pct: 5.0      # only accept new params if score improves by this %
```

### 10.10 Validation
- `test_registry.py` covers: save/load round-trip, regime-specific params, live feedback aggregation
- `test_live_runner.py` updated to cover `--use-trained-params` flag
- `test_optimizer.py` updated to cover `--include-live` flag
- `pytest tests/` passes (all existing + new tests green)
- Manual test: run optimizer → verify registry.json created → restart → verify params loadable

### 10.11 Constraints & Safety
- **Never auto-mutate config.yaml.** Registry is advisory; `config.yaml` remains the manual override.
- **Live runner remains deterministic** given the same parameters. Loading params from registry does not introduce randomness.
- **No ML models.** This is parameter persistence, not model inference.
- **Feedback is append-only.** Live trades are never deleted from the registry.

---

## 11. Out of Scope
- Docker Compose setup
- Authentication/authorization on API
- Real-time WebSocket streaming from cTrader MCP (not supported by server)
- ML models in the live trading loop (prohibited by project design)
- Automatic online learning without human review (feedback informs, but human must trigger re-training)
