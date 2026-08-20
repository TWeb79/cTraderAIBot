# Implementation Plan — cTrader Anthropic Bot (Project 58)

**Version:** 0.7.4
**Date:** 2026-08-19 (original) · **Last reviewed:** 2026-08-20 · **Implementation started:** 2026-08-19
**Author:** Inventions4All - github:TWeb79

> **v0.7.4 (2026-08-20, implemented):** direct follow-up to v0.7.3, on user
> report "it is not trading yet and i am not sure if the training works."
> Full details in new §18. Summary:
> 1. **Confirmed via the real device**: no process — neither Docker nor a
>    manual terminal — is currently running `execution/live_runner.py` (or
>    even the dashboard API). This is very likely the actual, immediate
>    reason nothing is trading right now, independent of any code fix. See
>    §18.1.
> 2. **Found and fixed a real training bug**: every dashboard-triggered
>    "optimize" job was silently saving `best_params: {...all null...}` to
>    `data/reports/parameter_registry.json` — confirmed on the real device's
>    own registry file (`performance.total_return_pct: 106.6`,
>    `n_trades: 611`, but every param `null`). Root cause: `_run_backtest_sync()`
>    in `training/optimizer.py` returned params nested under a `"params"`
>    dict key; `pd.DataFrame(results)` in `optimize()` then had no
>    `level_proximity_atr_mult`/etc. *columns* at all, so
>    `dashboard_api.py`'s `row.get("level_proximity_atr_mult")`-style
>    extraction always got `None`. This meant `--use-trained-params` and the
>    dashboard's "Use trained params" toggle (which **is** turned on in the
>    real device's `.auto_control.json`) silently applied nothing, ever,
>    with no visible error. Fixed by flattening params directly into the
>    returned dict; `dashboard_api.py`'s save path also now refuses to
>    persist an all-None result instead of silently writing it. Zero test
>    coverage existed for `training/optimizer.py` before this — new
>    `tests/test_optimizer.py` covers the DataFrame shape directly so this
>    class of bug can't silently regress again.
> 3. **New "Live status" dashboard panel** — surfaces
>    `execution/live_runner.py`'s own account of why it did or didn't trade
>    on its last cycle (kill switch / no data from MCP / no signal /
>    auto-mode or strategy gating / sizing failure / order placed), via a
>    new `_write_cycle_status()` writing `data/cache/.last_cycle_status.json`
>    and `GET /api/live-status` reading it back. Previously every one of
>    these decision points only ever `print()`ed to stdout — invisible
>    unless a human was tailing the live runner's own console/container
>    logs. Directly targets "not sure if it's trading" going forward.
> 4. **Dashboard kill switch** — closes the §17.4 gap:
>    `create_kill_switch()`/`check_kill_switch()` always existed but were
>    only reachable by a human `touch`ing the file directly. New
>    `GET /api/kill-switch` / `POST /api/kill-switch/set {"active": bool}`
>    plus a toggle button in the new Live status panel.
>
> Full test suite: **166/166 passing** (157 + 3 new `test_optimizer.py`
> tests for the DataFrame-shape fix + 6 new `test_live_runner.py` tests for
> the cycle-status writes). All new/changed dashboard JS modules
> (`live-status.js`, `api.js`, `app.js`) re-verified as syntactically valid
> ES modules.

> **v0.7.3 (2026-08-20, implemented):** audit-and-fix batch on direct user
> request ("review the project and fix everything which stops it from
> automatically trading"). Full details, including what was investigated and
> ruled out, in new §17. Summary of what changed:
> 1. **Daily-loss circuit breaker was dead in live trading.**
>    `risk_manager.start_new_session()` was only ever called from
>    `backtest/engine.py`'s `run_backtest()` — never from
>    `execution/live_runner.py` — so `day_start_equity` stayed `0.0` forever
>    and `record_realized_pnl()`'s `if self.day_start_equity > 0` guard meant
>    `halted_today` could never trip. `run_one_cycle()` now starts a new risk
>    session (fetching current equity) whenever the enriched bars' own
>    `session_date` rolls over, mirroring the backtest engine's per-bar logic.
> 2. **A fresh/reset demo account could never place its first trade.**
>    `estimate_value_per_point_per_lot()` correctly refuses to guess a
>    value when there's no historical deal data for the symbol (by design),
>    but `_execute_trade()` had no escape hatch — it just aborted every time,
>    forever, for an account with zero deals. New opt-in
>    `risk.value_per_point_per_lot_fallback` (`config/config.yaml`, default
>    `null`) lets a human supply an explicit value; used only when the
>    empirical estimate is `None`, and clearly logged as a fallback (not an
>    empirically-derived value) whenever it's used.
> 3. **A Docker deployment never traded at all.** `docker-compose.yml`
>    defined only `api` and `dashboard` — no service ever ran
>    `execution/live_runner.py`. New `live_runner` service
>    (`Dockerfile.live_runner`) with `restart: unless-stopped`, same
>    `host.docker.internal:9876` MCP URL and volume-mount pattern as `api`.
> 4. **A dropped/failed MCP connection crashed the whole process.**
>    `run_live()`'s `async with CTraderMCPClient(...)` was outside any
>    try/except, so any connection failure — including this process starting
>    before the cTrader desktop app is ready — was an uncaught crash with no
>    restart. It's now inside a retry loop (log, wait `poll_interval_seconds`,
>    retry) that only exits via the kill switch, same as the existing
>    per-cycle retry already did for cycle-level failures.
> 5. **`config.yaml`'s `execution.dry_run_default` was dead config** — the
>    real default was always `dry_run=False` (live orders) regardless of what
>    it said, since `--dry-run` was a `store_true` flag with no way to
>    express "false" from the CLI. Now wired up in `main()`: an explicit
>    `--dry-run`/new `--live` flag always wins; with neither passed,
>    `config.yaml` decides. Shipped as `false` so this fix does not silently
>    change any existing no-flags deployment's behavior.
> 6. Stale docs/labels fixed: `scripts/run_live.py`'s docstring referenced a
>    `--force-live` flag that never existed; `Dockerfile.api`/
>    `Dockerfile.dashboard`'s `LABEL version="0.1.0"` hadn't moved since
>    v0.1.0; `README.md`'s **Version** header was still `0.1.0`; `README.md`
>    had no Docker section at all despite `docker-compose.yml` existing.
>
> Explicitly **not** changed: `create_kill_switch()` remains reachable only
> by a human `touch`ing the file directly (no dashboard UI toggle) — a real
> gap, but a missing safety *feature*, not something that stops automated
> trading, so left out of this batch; see §17.4 if a dashboard kill-switch
> button is wanted later. Also: a prior turn in this session incorrectly told
> the user to "rebuild your `api`/`live_runner` containers" — no
> `live_runner` container existed before this batch; that guidance was wrong
> and is corrected by this entry.
>
> Full test suite: **157/157 passing** (146 existing — 2 of which
> [`test_run_live_asserts_demo_account`,
> `test_run_live_use_trained_params_flag`] were updated for the new
> connection-retry loop's extra `check_kill_switch()` call — + 11 new tests
> covering session rollover, the vpp fallback, MCP-reconnect retry, and the
> `dry_run_default`/`--live`/`--dry-run` resolution in `main()`).

> **v0.7.2 (2026-08-20, implemented):** two changes on direct user request:
> 1. **§15.2's Orderflow chart mode now shows the footprint itself, not a
>    proxy volume bar.** Switching the chart's toolbar to "Orderflow" now
>    replaces each visible candle's body with its buy/sell-by-price-level
>    footprint (the same data the single-candle click panel already showed),
>    instead of the old simple up/down tick-volume histogram bar. New bulk
>    endpoint `GET /api/bars/footprint?days=&timeframe=` computes every
>    visible candle's footprint in one MCP round trip (reusing the same
>    signal+profile bar fetch `GET /api/bars` already does — not one request
>    per candle), via a new pure helper `_footprints_by_candle()` in
>    `api/dashboard_api.py`. `chart.js` gained `setFootprints()`/a
>    `state.footprints` map keyed by bar timestamp, consulted in the
>    Orderflow render branch; it falls back to the old tick-volume bar for
>    any candle whose footprint hasn't loaded yet (still fetching, or
>    outside the fetched range) rather than leaving a gap. Fetched lazily —
>    only once the user actually activates Orderflow mode (via the toolbar
>    button, a days-range change, or the 15s poll while that mode is
>    active) — candle mode pays no extra cost. The single-candle click panel
>    (§15.2's original feature) is unchanged and still useful for exact
>    numbers.
> 2. **The dashboard's version number now actually moves.** It was hardcoded
>    to "0.1.0" in three places (`api/dashboard_api.py`'s `/api/version`,
>    `pyproject.toml`, `dashboard/index.html`'s fallback) and had never been
>    bumped despite four implementation batches. All three now read
>    `APP_VERSION = "0.7.2"` (a single constant near the top of
>    `dashboard_api.py`), kept in sync with this document's own **Version:**
>    header. **Going forward, bump `APP_VERSION`/`APP_BUILD_TIME` in
>    `api/dashboard_api.py`, `pyproject.toml`'s `version`, and this
>    document's header with every batch of changes** — do not let this
>    silently drift back to a frozen number.
>
> Full test suite: **146/146 passing** (142 + 4 new tests for
> `_footprints_by_candle`'s candle-boundary bucketing). All dashboard JS
> modules re-verified as syntactically valid ES modules.

> **v0.7.1 addendum (2026-08-20, planning only):** new §16 — a unified
> `Trade` domain object, the user's own idea. Right now "a trade" is four
> inconsistent shapes across `backtest/engine.py`, `execution/live_runner.py`,
> `journal/store.py`, and `training/simulator.py`/`api/dashboard_api.py`'s
> remapping — real duplication that already caused two bugs fixed in batch 4
> (the `opened_at`/`pnl` retrofits). §16 designs a single `Trade` object
> covering the full signal→sizing→fill→trailing-amendments→close→reflection
> lifecycle, confirmed with the user as design-only for now (not implemented).

> **Implementation checkpoint (2026-08-20, batch 4 — most of §15 built):**
> per the explicit request "review the implementationplan.md and implement
> everything which is needed for the next version", this batch implements
> the majority of §15's roadmap. **Implemented, tested, backward-compatible
> and off-by-default unless noted:**
> §15.2 orderflow footprint (`GET /api/bars/{timestamp}/footprint` — a
> tick-volume buy/sell-by-price-level proxy built from M1 sub-bars, wired
> into the dashboard: click a candle to inspect it — see the new "Orderflow
> footprint" sidebar panel); §15.3 MACD/VWAP/EMA indicators
> (`indicators/macd.py`, `indicators/vwap.py`, `indicators/moving_averages.py`,
> new, plus `vwap`/`ema_fast`/`ema_slow` columns always attached in
> `prepare_backtest_bars()`) and M15 "macro" MACD confirmation, wired into
> `live_runner.py` only, gated by `signals.require_macro_confirmation`
> (default `false`) — **not** threaded into the optimizer/simulator/backtest
> paths in this pass, so a trained-params or simulate run never sees macro
> data even if the flag is on; §15.4 always-visible chart prediction (the
> overlay now defaults on whenever auto mode is off, syncing on every
> auto-mode toggle — `app.js`'s `syncOverlayWithAuto`); §15.5 trade-history
> hover (the journal rows have a native tooltip: predicted direction/price
> vs actual outcome, sourced from the new `decision_json` column in
> `GET /api/journal`) + a Performance summary panel (win-rate/total-P&L/avg-R,
> `journal.aggregate_stats()`'s new `total_pnl`) — this also fixes the
> `opened_at == closed_at` bug: `record_trade()` now accepts a real entry
> timestamp, captured in `live_runner.py` at order-placement time; §15.6
> margin-%-of-free-margin position sizing (`risk.position_sizing_mode:
> "margin_pct"`, capping — never exceeding — the existing risk_pct-based
> size); §15.7 fixed 3:1 reward:risk (`risk.enforce_fixed_rr` +
> `risk.target_rr_ratio`); §15.8 / §15.8.1 trailing stop + TP-extension
> (`risk.trailing_stop.*`, pure/unit-tested math in
> `risk_manager.trailing_stop_update()` — the stop is provably never allowed
> to move backward, per `stop_improves()`); §15.9 VWAP/EMA bounce strategies
> (`signals.enable_bounce_strategies`, new `vwap_bounce`/`ema_bounce` signal
> reasons in `strategy/signals.py`).
>
> **Explicitly deferred, not built this pass** (see each subsection for the
> reasoning): §15.1's gradient-boosted-tree ML confidence layer (a genuinely
> separate project — new training pipeline, a persisted model file, a new
> `analysis/predictor.py` integration seam — better scoped as its own pass
> once the deterministic features above have live trade data to validate
> against) and §15.10's full "quantum terminal" visual redesign (a
> taste/design decision that deserves the user's direct input, not a
> unilateral rewrite of a UI already described as "awesome").
>
> Every new feature is config-gated and defaults to the exact prior
> behavior — a `config.yaml` with none of the new keys set behaves
> identically to before this batch. Full Python test suite: **142/142
> passing** (up from 93 at the start of this batch — 49 new tests covering
> the new indicators, signal branches, risk-manager pure functions, journal
> schema fields, live-runner execution paths, and the footprint bucketing
> logic). All dashboard JS modules re-verified as syntactically valid ES
> modules (`node --check`).
>
> **Known limitations carried forward honestly, not hidden:** the orderflow
> footprint is a tick-volume proxy (buy/sell classified by each sub-bar's
> close-vs-open direction), not true Level-2 bid/ask depth — no such MCP
> tool exists (see §15.2's original feasibility research, unchanged). Macro
> MACD confirmation only affects the live runner, not backtests/optimizer/
> simulator, so backtested performance with `require_macro_confirmation:
> true` does not yet reflect what live trading would actually do with that
> flag on — treat any such backtest as optimistic until that gap is closed.
>
> **You will not see any of this in the browser until you refresh, and it
> may need a container rebuild:** same as every prior batch — rebuild
> `api`/`live_runner` containers if running via Docker, then hard-refresh
> the dashboard tab.

> **Planning checkpoint (2026-08-19, §15 added — nothing in §15 is
> implemented yet, by explicit request):** §15 is a new, thoroughly
> researched roadmap covering everything requested in this pass — an AI/ML
> self-optimization recommendation (gradient-boosted confidence layer, not
> a neural net — see §15.1 for the full reasoning), an orderflow/demand
> feasibility review (true Level-2 depth is very likely unavailable via the
> cTrader Local MCP server — see §15.2's findings and its footprint-based
> alternative), 15-min MACD/VWAP background analysis (§15.3, reusing the
> `timeframes.htf` config key that already exists but was never wired up),
> always-visible chart predictions (§15.4), trade-history hover markers +
> a performance panel (§15.5, which found a real existing bug —
> `opened_at`/`closed_at` are currently stamped identically), margin-%
> position sizing (§15.6), a fixed 3:1 risk:reward mode (§15.7), a
> breakeven-plus trailing stop (§15.8), VWAP/EMA/session-POC bounce
> strategies (§15.9), a "quant terminal" UI direction (§15.10), and a
> suggested build order (§15.11). Every item is grounded in the actual
> current code (exact file/function references), not assumptions — see
> §15's intro for what `📋 planned` / `🔎 needs a feasibility spike` /
> `❓ open question` mean.
>
> **v0.6.1 addendum:** §15.8 gained a new §15.8.1 — a direct follow-up
> request resolving §15.8's own "should trailing continue past the first
> jump" open question: once price approaches the take-profit, extend TP by
> +5 pips and ratchet the stop-loss closer to price, with a hard,
> explicitly-stated invariant that the stop must **never** move backward.
> Confirmed with the user this stays planning-only for now (not
> implemented) — same as the rest of §15.

> **Implementation checkpoint (2026-08-19, batch 3):** on top of batch 2 (all
> of §10/§11), this batch adds three user-requested features documented in
> new §14: the Training panel's "simulated trades vs prediction" analysis
> window, an activatable chart overlay for open positions + the predicted
> next-5min price, and a one-click "execute predicted trade" button. See §14
> for the full design, especially the manual-trade execution routing (queued
> via file-based IPC and executed by `live_runner.py`, not placed directly by
> the dashboard API — a deliberate safety choice, confirmed with the user).
> Full Python test suite re-verified at 93/93 passing (85 + 8 new tests for
> the manual-trade IPC), all dashboard JS modules + CSS pass a
> syntax/brace-balance check, and `dashboard_api.py` re-verified importable
> with all new routes present.
>
> **You will not see any of this in the browser until you refresh, and it
> may need a container rebuild:** `live_runner.py` and `dashboard_api.py`
> both changed in this batch. If you're running via Docker, rebuild both —
> `docker compose build && docker compose up -d` — then hard-refresh
> (Cmd+Shift+R) the dashboard tab for the frontend changes. The "Execute
> trade" button only works while `live_runner.py` is actually running (it's
> the process that consumes the manual-trade request and places the order).

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

---

## 14. Training analysis window, chart trade overlay, manual execute (2026-08-19, batch 3)

Three features requested directly by the user in this session, on top of the
already-completed §10/§11 work.

### 14.1 — Training panel: narrower controls + "simulated trades vs prediction" window
**Status: ✅ done.** `dashboard/index.html`'s `.training__controls` (mode/
days/checkbox/buttons) is now wrapped with a sibling `.training__analysis`
panel inside a new `.training__body` flex row; controls are capped to 66%
width (`flex: 0 0 66%`) so the analysis window has room beside it (stacks
vertically under 900px).

Backend: `api/dashboard_api.py` gained `_simulation_trade_records()` /
`_simulation_summary()`, run after every completed `simulate` training job
and cached in a new module-level `LAST_SIMULATION` dict, exposed via new
`GET /api/training/trades?limit=N`. Each trade record pairs the
*predicted* direction/price (the deterministic signal's `side`/
`target_price` at entry — this project has no separate "AI prediction"
distinct from the signal itself, so "predicted" here means what
`evaluate_bar()` decided when the trade opened) against the *actual*
outcome (`exit_price`/`exit_reason`/`r_multiple`), plus `price_delta` and a
`direction_correct` flag (`exit_reason == "target"`). The summary aggregates
`direction_hit_rate`, `avg_r_multiple`, `avg_price_delta`/`avg_abs_price_delta`,
and a per-regime breakdown.

Frontend: `dashboard/js/training.js` gained `refreshSimulationAnalysis()`
(calls the new endpoint, renders summary stat tiles + a scrollable trade
table) — triggered on panel init and whenever `renderStatus()` sees a
completed `simulate` job (from either the WebSocket broadcast or the
fallback poll). Styled in `cockpit.css` (`.training__body`,
`.training__analysis*`, `.training__side--*`, `.training__result--*`).

### 14.2 — Activatable chart overlay: open positions + predicted next-5min price
**Status: ✅ done.** New toolbar button ("Overlay", off by default — the
user called it out as "activatable") in `.chart-panel__toolbar`. `chart.js`
gained per-`<svg>` `showOverlay` state plus `setOverlayEnabled()` /
`getOverlayEnabled()` / `updateChartExtras()` / `updateChartPrediction()`.
When on, `draw()` renders: (a) each open position's entry (solid line,
colored by side) / SL / TP (dashed) from `extras.positions`, labeled with
side + volume; (b) the current prediction's ENTRY/SL/TP lines, with the TP
line now labeled "PRED 5m (TP)" to make clear it's `predict_next()`'s
next-5min target — the same price the manual-execute button (§14.3) would
use. The Y-axis range only stretches to fit these when the overlay is
actually on, so toggling it off doesn't waste chart vertical space on a
hidden line.

Position field names aren't documented anywhere in this codebase (the MCP
server's raw response shape) — `app.js`'s new `normalizePositions()`
defensively tries `entryPrice`/`entry_price`, `stopLoss`/`stop_loss`/`sl`,
`takeProfit`/`take_profit`/`tp`, `side`/`tradeSide`/`direction`, matching
the existing defensive pattern in `renderPosition()`. A position missing
SL/TP just doesn't draw that particular line rather than erroring.

`app.js` now tracks `latestPositions` (normalized on every WebSocket
snapshot) and calls `updateChartExtras()`/`updateChartPrediction()` on each
WS update, so the overlay refreshes immediately rather than waiting for the
next 15s `/api/bars` poll.

### 14.3 — One-click "execute predicted trade" (manual override)
**Status: ✅ done**, using the execution routing the user explicitly chose
when asked (see below) — through `live_runner.py`, not a direct order
placed by the dashboard API.

**The design question and the user's answer:** this places a real order, so
before implementing it the user was asked how the button should submit the
trade — (A) queue a request that `live_runner.py` (the same process that
already places automated trades) picks up on its next cycle and executes
through its existing risk-sized, kill-switch-respecting, journal-tracked
pipeline, vs (B) have the dashboard API call MCP directly and place it
immediately, bypassing that pipeline's daily-loss/open-risk tracking. **The
user chose (A).** This keeps exactly one process (`live_runner.py`) as the
source of truth for risk/position state, at the cost of the button not
being instantaneous (typically ~15s, one poll cycle) and only working while
`live_runner.py` is actually running.

**New sidebar panel** ("Predicted trade", `dashboard/index.html`, between
"Auto trading" and "Signal feed"): shows direction/likelihood/entry/SL/TP
(TP labeled "predicted price", per the request that TP = the predicted
price) and note/reason, with a single "Execute trade" button, disabled
unless the prediction is actionable (LONG/SHORT with all three prices
present) or a request is already pending.

**Backend (`api/dashboard_api.py`):**
- `refresh_loop()` now computes `STATE["auto"]` (the `predict_next()`
  result) unconditionally every cycle, no longer gated on `AUTO["enabled"]`
  — the manual-execute panel needs a live prediction whether or not
  auto-trading is switched on; `AUTO["enabled"]` still controls only
  whether the live runner *acts* on signals automatically.
- `POST /api/manual-trade`: refuses if the kill switch file exists, if a
  request is already pending, or if `STATE["auto"]` has no actionable
  LONG/SHORT prediction with entry/stop/target. Otherwise writes
  `data/cache/.manual_trade_request.json` using **the server's own current
  prediction**, not anything the client sends — a stale button click can't
  fire off outdated prices this way.
- `GET /api/manual-trade`: reports whether a request is still pending
  (i.e. not yet consumed by `live_runner.py`) plus kill-switch state, for
  the panel's "queued..." / "cleared" status line.

**`live_runner.py` refactor:** the order-sizing/placement/poll-until-close/
journal/registry-feedback logic that used to live inline in
`run_one_cycle()` is now a shared `_execute_trade()` function (same risk
sizing, min-stop-ATR floor, kill-switch, daily-loss/open-risk gate as
before — nothing about the automated path's behavior changed). New
`consume_manual_trade_request()` (mirrors `load_auto_control()`'s
file-based-IPC pattern, but read-and-delete so a request is attempted at
most once) is checked once per cycle, independent of that cycle's
`evaluate_bar()` signal — the user already confirmed direction/entry/SL/TP
via the dashboard, so it doesn't need to match whatever the automated
signal decides on the same bar. `_build_decision()`/`_build_reflection()`
were changed from taking a `Signal` object to taking explicit
side/entry/stop/target/reason params so both the automated and manual
paths can share them.

**Tests:** `tests/test_live_runner.py` gained
`test_run_one_cycle_manual_trade_request_is_executed`,
`test_run_one_cycle_manual_trade_request_respects_dry_run`,
`test_consume_manual_trade_request_reads_and_deletes_file`,
`test_consume_manual_trade_request_rejects_malformed` (parametrized over
bad direction/type/missing-field/non-JSON payloads), and
`test_consume_manual_trade_request_missing_file_returns_none`. Full suite:
93/93 passing.

**Not done / explicitly out of scope for this batch:** a confirmation
dialog beyond the single OK button (matches what the user asked for
literally — "just with one OK button"); volume/position-sizing overrides
(uses the same risk-per-trade % as automated trades, not user-editable);
demo-vs-live gating (matches existing `run_live.py` behavior, which already
places real orders on the demo account by default and only warns via
README, not a hard block).

---

## 15. Roadmap: AI self-optimization, orderflow, quant UI, and advanced risk/strategy (2026-08-19 planning · 2026-08-20 — §15.2–§15.9 implemented, see the batch-4 checkpoint at the top of this document)

Everything below was originally a design + feasibility review, explicitly
**not** implemented at the time it was written. As of the 2026-08-20 batch-4
checkpoint, §15.2 through §15.9 have been built (each subsection below now
says so at its top) — §15.1 (the ML confidence layer) and §15.10 (the full
UI redesign) remain design-only, deliberately deferred (see the checkpoint
for why). Each subsection was grounded by re-reading the actual current code
(not assumptions) — file/function references are exact. Status markers in
this section mean:

- `📋 planned` — design is settled enough to implement as described.
- `🔎 needs a feasibility spike first` — the design depends on something
  unverified (usually: does the MCP server actually expose a field/tool we
  need). Do the spike (see each item) before writing implementation code.
- `❓ open question` — a genuine judgment call the user should confirm
  before implementation, flagged rather than silently assumed.
- `✅ implemented (2026-08-20)` — built in batch 4; see the file references
  added inline for where.

### 15.1 — AI self-optimization: recommendation (gradient-boosted tree confidence model, not a neural network — yet)

**The question:** should this project add a neural network, and is ML a
good alternative to the current deterministic statistics engine
(`training/registry.py`)?

**Recommendation: add a gradient-boosted tree classifier as an *advisory
confidence layer* on top of the existing deterministic signal — do not add
a neural network now, and do not let ML choose trades or set prices.**

Reasoning, grounded in what's actually in this repo today:

1. **Data volume doesn't support a neural network yet.** This is a
   single-symbol, single-timeframe system; live trade count grows one
   trade at a time (`journal.record_trade`, called once per closed
   position). Neural nets (LSTM/Transformer/CNN-on-candles) need thousands
   to millions of labeled examples to generalize instead of memorizing
   noise — this system will realistically have dozens-to-low-hundreds of
   labeled trades for a long time, even with `training/simulator.py`'s
   historical replay padding the count. A gradient-boosted tree
   (LightGBM/XGBoost, or `sklearn.ensemble.HistGradientBoostingClassifier`
   to avoid a new heavy dependency) is dramatically more sample-efficient
   on tabular features like this and far less prone to memorizing noise
   at this scale.
2. **Interpretability is a stated project requirement, not a nice-to-have.**
   `training/registry.py`'s own docstring, `README.md`, `architecture.md`,
   and the dashboard's "Model learning" panel caption all explicitly claim
   *"no ML — only persisted numeric parameter sets + aggregate stats"* /
   *"100% deterministic... no LLM/ML in the trading loop."* A neural net is
   very hard to audit trade-by-trade ("why did it take this trade?"); a
   gradient-boosted tree exposes feature importances and SHAP values per
   prediction, which is auditable and keeps faith with that principle in
   spirit even once ML is added.
3. **The existing architecture already has exactly the right seam for
   this.** `analysis/predictor.py`'s `predict_next()` already separates
   "what trade, if any" (calls the *same* deterministic `evaluate_bar()`
   the live loop uses — untouched) from "how confident are we"
   (`_estimate_likelihood()`, currently a simple registry win-rate + live-
   feedback blend). That confidence step is the correct integration point:
   replace/augment `_estimate_likelihood()` with a trained classifier's
   `predict_proba()`, and optionally use it as an additional hard filter
   in `live_runner.run_one_cycle()` (e.g. skip the trade if predicted
   probability < a configurable `signals.min_ml_confidence`), layered
   **on top of**, not instead of, the existing risk gate
   (`risk/risk_manager.py`). The set of *candidate* signals stays 100%
   rule-based and reproducible; ML only scores/filters, it never invents a
   trade or sets an entry/stop/target price.

**Proposed design (for when this is implemented):**

- New `training/ml_model.py`: trains offline only (like `optimizer.py`/
  `retrain.py` — never inside the live loop), on the parameter registry's
  historical + simulated trades (`training/registry.py`,
  `training/simulator.py`'s trade records already have most of what's
  needed: `setup_tag`, `regime`, `entry_poc`/`entry_vah`/`entry_val`,
  `atr`, outcome). Add engineered features: distance-to-level in ATR
  units, the new pre-NY/NY session-split levels (§11.1, currently computed
  but unused by `signals.py` — see §15.9), time-of-day/day-of-week,
  session (Asia/Frankfurt/NY), ADX/DI, and — once added — MACD/VWAP
  distance (§15.3) and RR ratio. Label: did the trade hit target before
  stop (binary), or R-multiple (regression) — start with the binary
  classifier, it's simpler to validate and threshold.
- Persist the trained model as a versioned artifact next to the existing
  JSON registry (e.g. `data/reports/ml_confidence_model_v{n}.joblib`),
  loaded read-only by `analysis/predictor.py` and (if the hard-filter
  option is enabled) `execution/live_runner.py` — mirrors how
  `ParameterRegistry` is already loaded read-only by both.
- Retraining is offline and opt-in, reusing the existing
  `training.auto_retrain_after_trades` config concept (currently defined
  in `config.yaml` but wired only to the numeric-parameter retrain in
  `training/retrain.py` — extend it, or add a parallel
  `training.ml_retrain_after_trades`).
- New dashboard surface: extend the existing "Model learning" panel
  (`panels.js`'s `renderLearningGauge`/`refreshLearningSparkline`, already
  captioned as "a deterministic statistics engine, not a neural network")
  with a second, clearly-labeled section once ML is added — e.g. "ML
  confidence: 62% (gradient-boosted tree, trained on N trades, retrained
  2026-08-15)" — so the UI keeps being honest about what kind of model is
  actually running, per this project's existing transparency pattern.
- Backtest/validate exactly like `training/optimizer.py` already does for
  numeric params: walk-forward split (train on older data, validate on
  newer), never train and evaluate on the same window, and require the ML
  filter to beat the no-filter baseline's composite score by the same
  `min_improvement_pct` gate `ParameterRegistry.save_best_params()` already
  enforces before promoting new numeric params — apply the identical
  discipline to promoting a new ML model version.

**Explicitly not recommended now, revisit later:** a neural network
(LSTM/Transformer over raw price sequences, or a CNN over candle images).
Revisit once (a) live+simulated trade count is in the thousands, not
hundreds, and (b) the gradient-boosted baseline's feature importances show
there's meaningful *sequential* structure a tree model can't capture (trees
handle engineered tabular features well but not raw sequence patterns) —
at that point a small sequence model as a second opinion, still gated the
same way, would be a reasonable phase 2. Building it first, on this much
data, risks an unaudited black box that contradicts the project's own
stated design principle and is very likely to overfit.

### 15.2 — Orderflow / bid-ask demand visualization  `✅ implemented (2026-08-20)`

**v0.7.2 follow-up:** the chart's "Orderflow" toolbar mode now renders this
footprint data *in place of the candle itself* for every visible bar (not
just the one the user clicks) — see the v0.7.2 changelog entry above and
`_footprints_by_candle()` / `GET /api/bars/footprint` in
`api/dashboard_api.py`. The original single-candle click panel described
below is unchanged and still available for exact numbers.

**🔎 Feasibility spike required before design is final.** Re-reading
`mcp_cheatsheet.md` (this session) and `mcp_client.py`'s full method list:
the cTrader Local MCP server is **pull/request-response only, no
streaming/subscription**, and its documented "Analysis (market data)"
category is *"Live bid/ask, historical candles, spread, session high/low,
market news, deal/order history, portfolio exposure, risk checks"* — i.e.
**top-of-book bid/ask only**. There is no documented Level 2 / Depth-of-
Market (DOM) tool, and `mcp_client.py` has no `get_depth`/`subscribe_depth`
method. `US500` is a synthetic index CFD, not a real order-book instrument
even on venues that do offer DOM for FX pairs — a true "bids and asks at
each price level" ladder is very unlikely to be available for this symbol
at all, MCP or not.

**Action before any implementation:** run `scripts/discover_mcp_tools.py`
(already exists, exactly for this — its own docstring says
"reconcile [mcp_client.py] against whatever this script actually prints")
against the real running MCP server and check specifically for any
`depth`/`orderbook`/`level2`/`market_depth` tool. Do not assume the
cheatsheet's category list is exhaustive.

**If no real depth data exists (the expected outcome), recommended
alternative — a per-candle "footprint" / micro volume profile:**
this project already fetches a *finer* timeframe than the signal timeframe
purely to build the volume profile (`timeframes.profile`, default `M1`,
see `config.yaml` and `_fetch_data()` in `training/optimizer.py` /
`backtest/engine.py`). That's the actual usable substitute for order-book
depth: when the user zooms into one M5 candle on the chart, fetch/reuse
that candle's underlying M1 sub-bars and render a mini horizontal volume
histogram *within* the candle — i.e. the same volume-profile technique
`indicators/volume_profile.py` already applies per-session, applied at
single-candle granularity. This surfaces "which price level inside this
candle had the most volume" (a real, honest proxy for demand), which is
what the existing "Orderflow" chart mode (tick-volume up/down histogram,
`chart.js`) is a coarser version of — this would be the finer-grained
successor, not a replacement mode.

**Design sketch (for later):**
- New API endpoint `GET /api/bars/{timestamp}/footprint?timeframe=M5` in
  `dashboard_api.py`: fetches the M1 (or finer, whatever `discover_mcp_tools`
  confirms as the smallest available period) bars spanning that one M5
  candle's window, buckets them into N price bins (reuse
  `indicators/volume_profile.py`'s binning logic), returns
  `{bins: [{price_lo, price_hi, volume, buy_volume, sell_volume}]}` — buy/
  sell split via the same tick-rule proxy the "Orderflow" mode already uses
  (`close >= open` -> up-volume), since true buy/sell-initiated volume
  isn't available without real depth data either.
- Frontend: on candle click (or hover-and-hold) in `chart.js`, fetch the
  footprint and render a small horizontal histogram overlay anchored to
  that candle, color-coded like the existing volume-profile sidebar
  (POC-style highlight for the highest-volume price level in that candle).
- **UI copy must say "intra-candle volume distribution (tick-volume proxy)"
  or similar, not "order book" / "bids and asks"** — matching this
  project's existing honesty pattern around the "Orderflow" toggle's tooltip
  ("Tick-volume proxy, not true bid/ask orderflow") and the ML confidence
  panel's planned labeling in §15.1. Overclaiming data the MCP server
  doesn't actually provide would be worse than not building the feature.
- Feed into decision-making (as the user asked): once built, the same
  per-candle footprint bins can become engineered features for §15.1's ML
  model (e.g. "was the entry price near the highest-volume bin of the
  triggering candle") and/or a new `evaluate_bar()` condition (e.g. only
  take a level-bounce signal if the triggering candle's footprint shows
  volume concentrated in the direction of the trade) — flag as a follow-on
  once the base footprint data exists, not simultaneously.

### 15.3 — Background 15-minute (higher-timeframe) MACD/VWAP analysis  `✅ implemented (2026-08-20)`

**📋 planned.** Two indicators that don't exist anywhere in this codebase
today (`grep -rn "macd\|vwap" src/` returns nothing) need to be added, and
there's already an unused scaffold for exactly this: `config.yaml`'s
`timeframes.htf: "H1"` ("higher timeframe used for regime confirmation")
is defined but **never read by any code** (`grep -rn "htf\b" src/` returns
nothing) — this was clearly planned for and never wired up.

**Design:**
- New `indicators/macd.py` (standard 12/26/9 EMA-based MACD + signal line +
  histogram) and `indicators/vwap.py` (session-anchored VWAP, resetting at
  the same `session_rollover_utc_hour` the volume-profile/session-level
  code already uses in `strategy/levels.py` — reuse that rollover boundary
  rather than inventing a second one).
- Fetch a second timeframe series specifically for this — the user said
  "15min chart," which doesn't have to be the same as the existing
  `timeframes.htf` (H1) placeholder; recommend adding a distinct
  `timeframes.macro: "M15"` key (keep `htf` for a possible future H1 use,
  don't overload one config key for two different purposes) fetched
  alongside `signal_bars`/`profile_bars` in `_fetch_data()`
  (`training/optimizer.py`) and the live/backtest equivalents.
- Compute MACD histogram sign + trend and VWAP position (price vs VWAP,
  and VWAP slope) on that M15 series each cycle, expose as new columns
  (e.g. `macro_macd_bullish`, `macro_price_vs_vwap`) attached to the
  latest signal-timeframe bar the same way `regime`/`poc_prev`/etc. are
  attached today (`backtest/engine.py`'s `prepare_backtest_bars()`).
- Use as a **confirmation filter, not a new signal source**, consistent
  with how `regime` already gates which signal families fire in
  `evaluate_bar()`: e.g. only take `trend_pullback_poc` BUY signals if
  `macro_macd_bullish` and `price > macro_vwap`, the way the docstring for
  `config.yaml`'s `htf` field already implied ("used for regime
  confirmation"). This is additive/optional (config-flagged, e.g.
  `signals.require_macro_confirmation: false` default) so it doesn't
  silently change existing backtest results when first added.
- Also feed the same macro features into §15.1's ML model — "is price
  above/below the 15-min VWAP" and "15-min MACD histogram sign" are
  exactly the kind of higher-timeframe-direction features a confidence
  model benefits from.
- Dashboard: a small secondary sparkline/badge (not a full second chart,
  to avoid the "quant terminal" screen becoming noisy — see §15.10) showing
  current M15 MACD state + price-vs-VWAP, likely in the existing "Session
  levels" sidebar panel next to the core datapoints already there.

### 15.4 — Always-visible chart prediction + inline "react on it" when auto is off  `✅ implemented (2026-08-20)`

**❓ open question resolved with a recommendation, confirm before building:**
today (batch 3) the chart's prediction/position overlay is an opt-in
toggle (`chart.js`'s `showOverlay`, default **off** — "activatable" per the
prior request), and executing a prediction happens from the separate
"Predicted trade" sidebar panel (`trade.js`). The new ask is to see the
prediction on the chart proactively and react to it specifically when auto
mode is off.

**Recommendation:** default the overlay to **on** automatically whenever
`AUTO.enabled` is false, and default to a minimal/off state when
`AUTO.enabled` is true (the live runner is already acting on it
automatically then, so a constant on-chart overlay is more clutter than
signal). Concretely: in `app.js`'s `init()`/auto-state handling, call
`setOverlayEnabled(chartEl, !autoState.enabled)` whenever the auto-mode
state is fetched or changes (`panels.js`'s `initAutoControls()` already has
a single place — its `push()` function — where the confirmed server state
comes back; wire the overlay default there), while still letting the
toolbar button override it manually either way.

For the "react on it" half: add a small inline "Execute" affordance
directly on the chart overlay itself (e.g. a clickable label right next to
the "PRED 5m (TP)" line already drawn by `chart.js`'s `draw()`), calling
the same `submitManualTrade()` the sidebar panel's button already uses
(`trade.js`) — so the user isn't forced to look away from the chart to act.
Keep the sidebar "Predicted trade" panel too (it's still the place to see
likelihood/note/status text an SVG overlay can't comfortably hold); the
chart button is a shortcut, not a replacement.

### 15.5 — Trade history on the chart (hover: predicted vs actual) + performance summary panel  `✅ implemented (2026-08-20)`

**📋 planned, but requires a journal schema fix first — flagging a real gap
found during this review:** `journal/store.py`'s `record_trade()` currently
stamps **`opened_at` and `closed_at` to the same timestamp** (`now`, at the
moment the position closes and the trade is journaled — see
`live_runner.py`'s `_execute_trade()`, which only calls
`journal.record_trade()` after the position-close poll loop exits). There
is no true entry timestamp recorded anywhere today. Also, `get_trades()`
only selects `opened_at, closed_at, symbol, r_multiple, setup_tag,
reflection_json` — it doesn't select `decision_json`, so the entry/stop/
target prices used for the trade aren't even returned by `GET /api/journal`
today. And **raw currency P/L is never persisted** — only `r_multiple`
(`TradeReflection` has no `pnl` field, and `_build_reflection()` in
`live_runner.py` computes `pnl` locally but never stores it).

**Required schema/plumbing changes (before the chart-hover feature can be
built):**
1. `execution/live_runner.py`'s `_execute_trade()`: capture the real entry
   timestamp right after `placed = await mcp.place_market_order(...)`
   succeeds, pass it through to `journal.record_trade()` as a distinct
   `opened_at` (currently it isn't threaded through at all).
2. `journal/store.py`: add a `pnl` column (raw account-currency P/L, not
   just R-multiple) to the `trades` table and `TradeReflection` model;
   `record_trade()` already receives `pnl` as a parameter to
   `_build_reflection()` in the caller — just needs to also be persisted,
   not only used to compute `r_multiple`.
3. `get_trades()`: also select `decision_json` (already stored, just not
   queried) so entry/stop/target and the true `opened_at` are available to
   the frontend.
4. `GET /api/journal` (`dashboard_api.py`) already exposes `get_trades()`
   as-is — no endpoint change needed once the above three land, just a
   response-shape change downstream consumers (`app.js`'s
   `renderJournal()`) need to tolerate.

**Chart markers (once the above exists):** small triangle/dot markers on
the chart's time axis at each closed trade's `opened_at`, colored by
outcome (win=`--long`, loss=`--short`), reusing the same nearest-bar-index
technique `chart.js`'s session-marker code already uses
(`draw()`'s `markers.forEach` block matching `extras.sessionMarkers` to the
nearest visible bar — the trade-marker code would be a sibling block using
`extras.trades` the same way). On hover, an SVG `<title>` (cheapest, no new
DOM/tooltip framework needed, consistent with this project's
no-charting-library constraint) showing: predicted direction + predicted
price (`decision_json.action` + `decision_json.take_profit`, i.e. what
`_build_decision()` recorded at entry) vs actual (`reflection.outcome`,
`r_multiple`, and the new `pnl` field) — directly answering "assess the
quality" of past predictions the way §14.1's simulated-trades table already
does for backtested trades, now for real trade history too.

**Performance summary panel (📋 planned, data already mostly exists):**
`journal/store.py`'s `aggregate_stats()` already computes `n_trades`,
`win_rate`, `avg_r`, and a `by_tag` breakdown — it just isn't surfaced as
its own dashboard widget today (only used internally by
`journal.latest_digest()`'s context, via `GET /api/digest`). Add: (a) the
`pnl` sum once persisted (§15.5 point 2) for a real "overall P/L" number,
not just R-multiples; (b) a small "Performance" panel (new section, likely
near "Trade journal") showing overall P/L, win rate, and — matching the
literal ask "success rate of successful positive traded trades" — a
distinct "average R on winning trades only" stat alongside the overall win
rate, since those are two different things worth showing separately.

### 15.6 — Position sizing: volume as % of free margin  `✅ implemented (2026-08-20)`

**🔎 needs one field-name confirmation, otherwise 📋 planned.**
`mcp_cheatsheet.md` states the MCP server's "Account" category includes
"balance/equity/**free margin**" — but the exact JSON field name isn't
confirmed anywhere in this codebase (`mcp_client.py`'s `get_balance()` and
`get_account_statistics()` are untyped passthroughs). Confirm the field
name via `discover_mcp_tools.py` output (same spike as §15.2) or a live
`get_account_statistics()` call before implementing.

Current sizing (`risk/risk_manager.py`'s `size_trade()`) is purely
risk-based: `volume = risk_amount / (stop_distance * value_per_point_per_lot)`,
where `risk_amount` is a % of *equity*. The ask is a second mode sized off
*free margin* instead.

**Design:** add `risk.position_sizing_mode: "risk_pct" | "margin_pct"` to
`config.yaml` (default `"risk_pct"` — preserves current behavior exactly,
opt-in only). For `"margin_pct"`: fetch free margin from
`get_balance()`/`get_account_statistics()` (whichever field the spike
confirms), fetch per-lot margin requirement via the already-existing
`mcp_client.calculate_margin(symbol, volume=1, volume_type="lots")`
(defined, currently unused anywhere in the codebase), then
`volume = (free_margin * risk.volume_pct_of_margin / 100) / margin_per_lot`,
clamped to `minVolume`/`maxVolume`/`volumeStep` exactly like the existing
path.

**Recommended safety guard (not explicitly requested, but consistent with
this project's "hard-enforced risk rules" framing in
`risk_manager.py`'s own module docstring):** don't let margin-based sizing
alone determine position size — take `volume = min(margin_based_volume,
risk_based_volume)`, i.e. margin-% sets an upper bound but the existing
per-trade risk-% and daily-loss/open-risk gates (`can_open_new_trade()`,
`current_open_risk_amount()`) still apply underneath. A fixed % of free
margin can size a much larger position than the risk budget intends during
low-volatility (tight stop) conditions, defeating the daily-loss circuit
breaker's purpose. Flagging this as a recommendation, not silently deciding
it — confirm with the user before implementation whether margin-% should be
a hard override or a capped upper bound.

### 15.7 — Fixed 3:1 risk:reward ratio  `✅ implemented (2026-08-20)`

**📋 planned.** Today `evaluate_bar()` (`strategy/signals.py`) sets
`target_price` per signal family from level-based logic (measured moves,
`close_prev`, `poc_prev` — see the four `Signal(...)` return sites), which
produces whatever RR emerges naturally from the levels, not a fixed ratio.

**Design:** add `risk.target_rr_ratio: 3.0` and `risk.enforce_fixed_rr:
false` (default off — opt-in, preserves existing backtested behavior until
explicitly turned on and re-validated) to `config.yaml`. When enabled, after
`evaluate_bar()` computes `entry_price`/`stop_price` as it does today,
override `target_price = entry_price + target_rr_ratio * (entry_price -
stop_price)` for BUY (mirrored for SELL) **before** returning the `Signal`
— a single override point right before each `return Signal(...)`, or more
cleanly, a small wrapper `_apply_rr_override(signal, cfg)` called once at
the end of `evaluate_bar()` regardless of which branch produced the signal,
so all four signal families get consistent treatment without four separate
edits.

**Open question to confirm before implementing:** should this fully replace
the level-based target (e.g. `range_fade_vah`'s natural target of
`poc_prev`) or only apply when the level-based RR is *below* 3:1 (i.e.
`target = max(level_target_distance, 3 * stop_distance)` in the trade's
favorable direction)? Full replacement is simpler and matches "the risk
level should be 3:1" literally; the max-of-both version keeps
level-based targets when they're already better than 3:1. Recommend full
replacement for a first version (simpler, easier to reason about and
backtest), with the max-of-both variant as a documented follow-up if
backtesting shows it underperforms.

**Must be re-validated in backtest before going live**, same as every
other risk-parameter change in this codebase (`training/optimizer.py`
already exists for exactly this) — a fixed 3:1 target will hit far less
often than a level-based one in ranging conditions (further away = harder
to reach before price reverses), which could meaningfully change win rate
and needs measuring, not assuming.

### 15.8 — Trailing stop-loss (breakeven-plus jump at +3 pips)  `✅ implemented (2026-08-20)`

**📋 planned.** No trailing-stop logic exists anywhere today —
`execution/live_runner.py`'s post-order loop (`_execute_trade()`'s
`while True: await asyncio.sleep(POLL_SECONDS); current = await
mcp.get_positions(); ...`) only polls for closure, it never amends the
stop. `mcp_client.py`'s `amend_position(position_id, stop_loss=...,
take_profit=...)` already exists and is currently unused anywhere in the
codebase — this is exactly the primitive needed.

**Design, matching the user's stated rule exactly (trigger at +3 pips
profit, jump stop to entry price + 1.4 pips):**
- New `config.yaml` block:
  ```yaml
  risk:
    trailing_stop:
      enabled: false          # opt-in
      trigger_pips: 3.0
      lock_pips: 1.4
  ```
- In `_execute_trade()`'s existing poll loop, each iteration (already
  fetching `current = await mcp.get_positions()` every `POLL_SECONDS`):
  compute the position's current unrealized profit in pips from its
  live price vs `entry_price` (need the position's current market price —
  `get_positions()`'s response shape isn't confirmed to include one; if
  not, an extra `get_spot_prices(symbol)` call per poll iteration is cheap
  at `POLL_SECONDS` cadence). Once profit_pips >= `trigger_pips` **and**
  the trail hasn't already been applied for this `trade_id` (track a
  `set()` of already-trailed trade ids, or a boolean on a small per-trade
  state object — needed because `amend_position()` should fire exactly
  once, not every poll tick), call
  `mcp.amend_position(position_id, stop_loss=entry_price + lock_pips *
  pip_size)` for BUY (`entry_price - lock_pips * pip_size` for SELL).
- The initial breakeven-plus jump is a single one-shot event (matches "it
  should jump to..." literally), not continuous ratcheting on its own —
  see §15.8.1 below for what continues after it.
- Must respect the kill switch and existing position-tracking the same way
  order placement already does — this only calls `amend_position` on
  already-open, already-risk-managed positions, so it doesn't interact with
  `RiskManager.size_trade()`/`register_open_trade()` at all, only with
  positions already past that gate.
- Test with a mocked `amend_position` the same way `tests/test_live_runner.py`
  already mocks `place_market_order`/`get_positions` — assert it's called
  exactly once per qualifying trade, with the right stop price, and not
  called again on subsequent polls once already trailed.

#### 15.8.1 — Follow-on rule: extend TP + ratchet SL forward when price approaches the target (never loosen)  `✅ implemented (2026-08-20)`

**📋 planned — resolves the "should trailing continue past the first jump"
open question above: yes, via this specific rule**, requested as a direct
follow-up: *"if the price is close to the tp then extend the tp +5 and
move the sl closer to the price. do never go back with the sl."* This is
phase 2 of the same `risk.trailing_stop` mechanism — phase 1 (§15.8's
breakeven-plus jump) still fires once at `trigger_pips` profit; phase 2
then keeps running for the rest of the trade's life, letting winners run
instead of capping the trail at entry+1.4 pips forever.

**Rule, as stated:**
1. Each poll iteration (same loop as §15.8), check whether the current
   price is "close to" the position's current take-profit.
2. If so: extend `take_profit` by **+5 pips** in the trade's favor (further
   from entry), and move `stop_loss` closer to the current price (tighter
   than before).
3. **Hard invariant, stated explicitly by the user and non-negotiable in
   the implementation:** the stop-loss must never move backward (i.e.
   never *away* from the current price / back toward — or past — entry).
   Every `amend_position()` call in this rule must be preceded by a check
   that the proposed new stop is strictly more favorable than the
   position's last-known stop (higher for BUY, lower for SELL); if not,
   skip the amend entirely that tick. This applies on top of whatever
   phase-1 stop is already in place, so the two rules compose safely —
   phase 2 can only ever tighten further from where phase 1 left it, never
   undo it.
4. This can fire repeatedly as price keeps climbing toward each newly
   extended TP — i.e. an unbounded "let it run" mechanism as long as price
   keeps advancing, not a single second event. It only ever stops
   extending when price stalls or reverses (at which point the
   already-tightened SL, per the invariant, is what eventually closes the
   position).

**Two numeric parameters the request didn't specify — proposed defaults,
flag as tunable and confirm before implementing, not hardcode silently:**
- **"Close to the TP"** needs a concrete proximity threshold. Proposed:
  `risk.trailing_stop.tp_extend_trigger_pips` (e.g. default `5.0`, matching
  the extension size itself, i.e. "within one extension-step of the
  target") — or, more robustly against different instruments/volatility
  regimes, an ATR-relative threshold (`tp_extend_trigger_atr_mult`, e.g.
  `0.5 * atr`) reusing the same ATR-relative-distance pattern
  `signals.level_proximity_atr_mult` already uses elsewhere in this
  codebase, rather than a second unrelated fixed-pips config. Recommend
  the ATR-relative version for consistency with the rest of the risk/
  signal config, but confirm with the user — they specified pips ("+5")
  for the extension itself, so a pips-based trigger may be what they
  actually expect too.
- **"Move the sl closer to the price"** needs a concrete target distance,
  not just a direction. Proposed: `risk.trailing_stop.sl_trail_distance_pips`
  (e.g. default `3.0`–`5.0`, i.e. new SL = current price minus this many
  pips for BUY) — each time the rule fires, recompute
  `candidate_sl = current_price - sl_trail_distance_pips * pip_size` (BUY;
  mirrored for SELL), then apply the invariant check in point 3 above
  before amending. This makes phase 2 behave like a classic
  distance-behind-price trailing stop, but only re-evaluated at the same
  moments the TP extension fires (event-driven, matching how the request
  is phrased — "if the price is close to the tp then...") rather than
  every single poll tick regardless of TP proximity, which would be a
  different (also reasonable, but not what was asked for) design.

**Implementation shape (extends §15.8's design, same function/state):**
- Extend the `risk.trailing_stop` config block:
  ```yaml
  risk:
    trailing_stop:
      enabled: false
      trigger_pips: 3.0          # phase 1: breakeven-plus jump
      lock_pips: 1.4
      tp_extend_pips: 5.0        # phase 2: TP extension step
      tp_extend_trigger_pips: 5.0  # phase 2: "close to tp" threshold (or _atr_mult variant — confirm)
      sl_trail_distance_pips: 3.0  # phase 2: how close to bring SL to price
  ```
- Track per-trade state beyond phase 1's one-shot flag: the current
  effective `take_profit` (since it changes over the trade's life, diverging
  from the original order's TP) and the current effective `stop_loss` (to
  evaluate the "never loosen" invariant against — not the *original* stop,
  the *most recent* one this mechanism itself set, since that's the value
  that must never be retreated from).
- Every `amend_position()` call from this rule updates **both**
  `take_profit` and `stop_loss` in one call (the MCP method already
  supports both in one request — no need for two round trips).
- Tests: mock a price sequence that approaches, extends, approaches again,
  extends again, then reverses without hitting the (now-tightened) stop —
  assert `amend_position` is called once per genuine extension event, each
  call's `stop_loss` is strictly more favorable than the previous call's,
  and that a reversal that doesn't cross the current stop triggers no
  further amends (proving the invariant holds and nothing "goes back").

### 15.9 — Strategy expansion: bounce/rejection at VWAP, EMA, and session-split levels  `✅ implemented (2026-08-20)`

**📋 planned.** Partial groundwork already exists: `evaluate_bar()`
(`strategy/signals.py`) already implements level-bounce logic for the
*full-session* POC/VAH/VAL — `range_fade_vah`/`range_fade_val` (fade at
VAH/VAL back toward POC) and `trend_pullback_poc` (bounce off POC in a
trend) both use the same `near(level)` proximity check pattern. What's
missing, exactly matching the ask:
1. **VWAP and EMA as bounce levels** — neither indicator exists yet
   (§15.3 adds VWAP; a simple EMA, e.g. `indicators/regime.py`-adjacent
   `indicators/moving_averages.py` with a configurable period, would be new
   too). Add new signal reasons `vwap_bounce`/`ema_bounce` using the same
   `near(level)` + reversal pattern as `range_fade_vah`/`val`, gated by
   regime the same way the existing bounce signals are.
2. **The pre-NY/NY session-split levels are computed but completely
   unused by `evaluate_bar()`** — `strategy/levels.py`'s
   `compute_session_levels()` (§11.1, already implemented and tested)
   produces `poc_pre_ny_prev`/`poc_ny_prev`/etc., which are already
   plumbed all the way to the dashboard's "Session levels" panel and
   `training/simulator.py`'s trade snapshots (§11.1/§14.1), but
   `evaluate_bar()` itself still only reads the full-session
   `poc_prev`/`vah_prev`/`val_prev`. Add `ny_poc_bounce`/
   `pre_ny_poc_bounce` (or fold into the existing `range_fade_*`/
   `trend_pullback_poc` reasons with a sub-tag) so a bounce specifically
   off the NY session's own POC — often a stronger level intraday than the
   full 24h POC — becomes a distinct, taggable, backtestable signal family.
   This is the single highest-value item in this subsection since the data
   already exists and is already flowing through the whole pipeline — only
   `signals.py` needs to consume it.
3. **Confirmation, not just proximity:** today's `near(level)` check is
   purely a distance-in-ATR proximity test with no wick-rejection or
   multi-bar confirmation — recommend keeping that as `level_proximity_atr_mult`
   already does today for the existing signals (backward compatible), but
   consider a config-gated stricter variant (e.g. require the bar's
   low/high to have touched the level and closed back on the other side of
   it — a real rejection, not just "close is near the level") for the new
   VWAP/EMA/session-POC signals specifically, since more signal families
   raises curve-fitting risk in `training/optimizer.py`'s grid search
   (more free parameters to overfit against a finite backtest window).
4. **Feed the expanded signal set through §15.1's ML confidence layer once
   both exist** — more signal families with varying reliability is exactly
   what a confidence classifier is good at ranking/filtering, rather than
   trusting every new bounce family equally by default the way a purely
   rule-based system has to.

### 15.10 — "Quantum trading environment" UI direction

This is a subjective/aesthetic brief — interpreting it as: a denser,
higher-contrast "quant terminal" visual language on top of the existing
dark cockpit theme (`cockpit.css`'s existing `--bg`/`--panel`/`--cyan`/
`--amber`/`--long`/`--short` palette stays; this is about density and
motion, not a new color system). Proposed direction, to confirm/adjust
with the user before any implementation (this is the one item in this
section closest to "purely a taste call" — worth a quick mockup or even
just a written confirmation before spending build time):

- **Higher information density**: smaller multiples throughout — e.g. tiny
  M15/H1 sparkline mini-charts (feeding off §15.3's macro data) living in
  the sidebar rather than a full second chart, more stat tiles (à la the
  new §15.5 performance panel and §14.1's simulated-trades summary,
  already built in that style) rather than prose.
- **Live-state motion**: brief glow/pulse animation (the codebase already
  has a `wizard-pulse` keyframe in `cockpit.css`, reusable) on numbers when
  they update — price ticks, P/L changes, a new prediction — reinforcing
  "this is live" without needing literal streaming (the MCP server is
  poll-based per §15.2, so the *appearance* of continuous motion has to
  come from the UI layer, not the data layer).
- **A persistent top-of-frame status strip**: symbol, live bid/ask spread,
  current regime, current ML confidence (once §15.1 lands), kill-switch
  state — always visible without scrolling, terminal-style.
- **Feature-importance / "why" readout** once §15.1's ML layer exists — a
  small horizontal bar chart of top contributing features per prediction,
  which is both genuinely useful (this is what makes an ML confidence
  score trustworthy rather than a black box) and visually fits a "quant"
  aesthetic better than a plain percentage.
- Keep the existing honesty-in-labeling pattern (§15.1, §15.2) — a "quantum"
  aesthetic should not imply quantum computing or literal AI sentience is
  involved; it's a visual style, and the UI copy should stay as precise
  about what's actually running as it is today.

### 15.11 — Suggested build order (once any of this is approved for implementation)

Roughly in dependency/risk order, not necessarily the order the user asked
for these — later items build on earlier ones or are safe to parallelize:

1. **§15.5's journal schema fix** (opened_at, pnl, decision_json in
   `get_trades()`) — small, safe, unblocks the performance panel and chart
   trade-markers, and is a real existing bug (opened_at == closed_at today)
   worth fixing regardless of the rest.
2. **§15.2/§15.6's feasibility spikes** (`discover_mcp_tools.py` run) —
   cheap, and several other items' designs depend on the answer.
3. **§15.3 (MACD/VWAP)** and **§15.9 point 2 (session-split-level bounce
   signals)** — pure additions, backward compatible behind config flags,
   independently backtestable via the existing `training/optimizer.py` /
   `backtest_runner.py` before ever touching live trading.
4. **§15.7 (3:1 RR)** and **§15.8 (trailing stop)** — both directly change
   live risk/reward behavior; build behind the proposed opt-in config
   flags, backtest first, and treat as higher-stakes than (3) given they
   change money-handling logic, not just signal generation.
5. **§15.6 (margin-% sizing)** — same caution as (4); needs the
   feasibility spike from (2) first regardless.
6. **§15.1 (ML confidence layer)** — biggest single item; benefits from
   (3)/(9) already existing first (more/better features to train on) and
   from (1) (accurate P/L for labeling). Build and backtest-validate
   thoroughly (walk-forward, `min_improvement_pct`-gated promotion like
   the existing numeric-param registry) before ever wiring the optional
   hard-filter into the live loop — ship it as a display-only confidence
   score first, add the filter as a later, separately-confirmed step.
7. **§15.4 (chart prediction defaults) and §15.10 (UI direction)** — pure
   frontend, lowest risk, can happen anytime; §15.10 specifically benefits
   from having something to visualize (ML confidence, macro sparklines)
   before being worth the polish pass.

## 16. A unified `Trade` domain object (2026-08-20 — PLANNING ONLY, not implemented; user's idea, confirmed as a design-only addition for now)

### 16.0 — The problem this solves

"A trade" currently exists as **four separate, inconsistent shapes** in this
codebase, and that duplication is not hypothetical — it already caused two
real bugs fixed in batch 4 (§15.5's `opened_at == closed_at`, and `pnl`
missing from `TradeReflection` until this pass added it as an afterthought):

1. **`backtest/engine.py`'s `Trade` dataclass** — `id, side, reason, regime,
   entry_time, entry_price, stop_price, target_price, volume, exit_time,
   exit_price, exit_reason, pnl`. Used only inside `run_backtest()`'s loop.
2. **`execution/live_runner.py`'s `_execute_trade()`** — no object at all;
   a trade's state is loose local variables threaded through one long
   function and its polling `while` loop: `entry_price, raw_stop_price,
   target_price, stop_distance, stop_price, volume, risk_amount,
   position_id, trade_id, opened_at, current_stop, current_target
   (mutated in-place by the §15.8 trailing-stop logic), pnl`.
3. **`journal/store.py`'s `TradeDecision` + `TradeReflection`** — the
   persisted shape, two separate pydantic models serialized into two JSON
   columns (`decision_json`, `reflection_json`) plus a handful of flat
   columns (`opened_at, closed_at, symbol, r_multiple, setup_tag`).
4. **`training/simulator.py`'s DataFrame rows**, which
   `api/dashboard_api.py`'s `_simulation_trade_records()` then *remaps into
   a fifth shape* just for the dashboard — notably renaming `target_price`
   to `predicted_price`, because the simulator's column names don't match
   what the "simulated trades vs prediction" UI wants to call them.

Consequences of this today: every new piece of trade data (this batch added
`pnl`, `opened_at`, `decision_json` access) has to be threaded through 3-4
call sites by hand, with no guarantee they stay consistent; the trailing
stop's amendment history (§15.8) only exists in `print()` log lines, not as
queryable data; and the dashboard has a whole function
(`_simulation_trade_records`) that exists purely to translate between
inconsistent naming.

### 16.1 — Proposed design

A single `Trade` object, created once when a signal is acted on (or a
manual/simulated trade begins) and carried through its entire life —
signal → sizing → fill → any trailing-stop amendments → close → reflection.
Proposed home: a new `src/ctrader_bot/domain/trade.py` (a neutral module
below `strategy`/`journal`/`backtest`/`execution` in the dependency graph,
so nothing existing needs to change what it imports from). Implemented as a
pydantic `BaseModel` (not a plain dataclass) since it already needs to
serialize to JSON for the journal/API — the extra validation cost is
negligible at "one object per trade", not "one object per bar".

Proposed fields, grouped by lifecycle stage:

```python
class TradeAmendment(BaseModel):
    at: str                # ISO timestamp
    stop_price: float
    target_price: float | None
    reason: str             # e.g. "trailing_lock", "tp_extend", "manual"

class Trade(BaseModel):
    # Identity
    id: str                 # position_id (live) or uuid4 (backtest/sim)
    symbol: str

    # Signal snapshot — the *plan* as evaluate_bar() produced it, before any
    # risk-management adjustments. This is the "predicted" side of every
    # predicted-vs-actual comparison (journal hover, simulator analysis
    # window) — no more renaming target_price to predicted_price downstream.
    side: Side
    setup_tag: str           # evaluate_bar()'s `reason`, e.g. "range_fade_vah"
    regime: Regime
    atr_at_entry: float | None
    signal_entry_price: float
    signal_stop_price: float
    signal_target_price: float

    # Risk/sizing snapshot — an audit trail of *why* the trade was sized the
    # way it was, which today isn't recorded anywhere.
    sizing_mode: str          # "risk_pct" | "margin_pct"
    stop_distance: float      # post min_stop_atr_mult floor
    rr_ratio_used: float | None   # set when risk.enforce_fixed_rr overrode the target
    volume: float
    risk_amount: float

    # Actual fill
    entry_price: float        # may differ from signal_entry_price (spread, slippage)
    opened_at: str             # ISO timestamp — fixes §15.5's opened_at bug at the source

    # Live lifecycle (mutated only by the §15.8 trailing-stop loop today)
    stop_price: float          # current — starts equal to signal-derived stop
    target_price: float | None # current — starts equal to signal_target_price
    amendments: list[TradeAmendment] = []

    # Close
    exit_price: float | None = None
    closed_at: str | None = None
    exit_reason: str | None = None   # "stop" | "target" | "manual" | "kill_switch" | "end_of_data"

    # Outcome
    pnl: float | None = None
    r_multiple: float | None = None
    outcome: str | None = None       # "WIN" | "LOSS" | "BREAKEVEN"
    lesson: str | None = None        # optional prose reflection (live only; backtest/sim leave it None)
```

Key methods:

- `apply_amendment(new_stop, new_target, reason, at)` — the *one* place the
  §15.8.1 "never move the stop backward" invariant is enforced
  (`risk_manager.stop_improves()`), appends a `TradeAmendment`, and updates
  `stop_price`/`target_price`. Replaces the loose `current_stop`/
  `current_target` locals in `_execute_trade()`'s poll loop.
- `close(exit_price, closed_at, exit_reason, pnl)` — computes `r_multiple`
  and `outcome` in one place instead of `_build_reflection()`'s separate
  logic.
- `to_decision() -> journal.store.TradeDecision` and
  `to_reflection() -> journal.store.TradeReflection` — so `journal/store.py`
  and its SQLite schema **do not need to change at all**; `Trade` becomes
  the thing that *assembles* what already gets persisted, not a
  replacement for the persistence format. This keeps the migration
  low-risk: phase 1 (below) touches no database schema.
- `to_dict()` — one JSON shape for the dashboard, replacing
  `_simulation_trade_records()`'s remapping and `normalizePositions()`-style
  ad hoc field guessing wherever a `Trade` (rather than a raw MCP position
  dict) is the source.

### 16.2 — What it replaces / how each consumer changes

- **`backtest/engine.py`**: `run_backtest()` constructs a `Trade` instead of
  its own dataclass at entry, mutates it at exit; `Trade` becomes the
  dataclass (its current 13 fields are a subset of the proposed ones, so
  this is a superset replacement, not a rewrite of the exit logic).
- **`execution/live_runner.py`**: `_execute_trade()` constructs a `Trade`
  right after order placement (once `position_id` is known), the trailing
  loop calls `trade.apply_amendment(...)` instead of reassigning
  `current_stop`/`current_target`, and the close path calls `trade.close()`
  then `journal.record_trade(trade.to_decision(), trade.to_reflection(),
  symbol, opened_at=trade.opened_at)` — the journal call itself is
  unchanged, just fed from the object instead of loose locals.
- **`training/simulator.py`**: constructs a `Trade` per simulated trade
  instead of a DataFrame row; `simulate()`'s return value becomes
  `list[Trade]` (or a DataFrame built *from* `Trade.to_dict()` for callers
  that still want a DataFrame, e.g. `append_simulated_to_registry`'s
  existing vectorized stats).
- **`api/dashboard_api.py`**: `_simulation_trade_records()` shrinks to
  `[t.to_dict() for t in trades]` — the manual renaming goes away entirely.
- **`journal/store.py`**: unchanged in phase 1 (see above). A phase 2 could
  later store `trade.model_dump_json()` directly instead of two separate
  JSON columns, but that's a real schema migration and shouldn't be bundled
  with phase 1.

### 16.3 — Costs and risks (being upfront, not just selling the idea)

- This is a **cross-cutting refactor**, not an additive feature — it
  touches `backtest/engine.py`, `execution/live_runner.py`,
  `training/simulator.py`, and `api/dashboard_api.py`, all of which changed
  in batch 4 (§15). It should be a dedicated pass with its own full
  regression run, not bundled into an unrelated feature batch.
- Needs a decision on `training/simulator.py`'s return type
  (`list[Trade]` vs "DataFrame built from `Trade`s") before touching code —
  `append_simulated_to_registry()` and the optimizer's scoring currently
  assume a DataFrame with vectorized `.mean()`/`.sum()` calls; either keep
  a `pd.DataFrame([t.to_dict() for t in trades])` conversion at the
  boundary, or accept rewriting that scoring code too. Recommend the
  former for phase 1 (smaller diff).
- `domain/trade.py` importing `Side` (from `strategy.signals`) and `Regime`
  (from `indicators.regime`) is safe — both are leaf-ish modules with no
  upward dependencies — but should be double-checked for import cycles at
  implementation time, since `journal/store.py` is deliberately decoupled
  today (its own docstring: "Single source of truth for trade history...
  used by both the live runner and the backtest runner") and `Trade`'s
  `to_decision()`/`to_reflection()` methods would be the first thing to
  import `journal.store` types into a non-journal module — the dependency
  direction should be `domain.trade -> journal.store`, never the reverse.

### 16.4 — Suggested build order

1. Add `domain/trade.py` with the `Trade`/`TradeAmendment` models and unit
   tests for `apply_amendment()` (including the "never move stop backward"
   invariant) and `close()`'s r_multiple/outcome computation — pure, no
   integration yet.
2. Wire into `backtest/engine.py` first (smallest blast radius — no live
   money, no MCP calls, existing tests give a tight regression net).
3. Wire into `training/simulator.py`, then `api/dashboard_api.py`'s
   `_simulation_trade_records()` (deleting the remap once `Trade.to_dict()`
   covers it).
4. Wire into `execution/live_runner.py`'s `_execute_trade()` last (highest
   stakes — real orders) and only after (2)/(3) have proven the object's
   shape is right in lower-stakes contexts first.

## 17. Audit: "everything which stops it from automatically trading" (2026-08-20 — implemented, see v0.7.3 changelog above)

User's request was open-ended ("review the project and fix everything which
stops it from automatically trading"), so this section documents what was
actually checked, what was found, and — importantly — what was deliberately
**not** treated as a blocker, so a future reviewer doesn't have to redo this
investigation from scratch.

### 17.1 — Findings, in order of how directly they block trading

1. **No Docker service ever ran the trading loop** (blocks trading
   completely, for any Docker-based deployment). `docker-compose.yml`
   defined `api` (dashboard backend) and `dashboard` (static files) only.
   Neither runs `execution/live_runner.py`. Confirmed via
   `Dockerfile.api`/`Dockerfile.dashboard`'s `CMD`s (uvicorn / `http.server`,
   nothing else) and by grepping for any supervisor/systemd/pm2/cron
   mechanism anywhere in the repo — none exists. `README.md` documents
   running `live_runner` as a separate manual terminal process
   (`.venv/bin/python scripts/run_live.py`), and never mentions Docker at
   all for it — this was a by-design gap in the original implementation,
   not a regression. **Fixed**: new `live_runner` Docker service.
2. **A fresh/reset demo account could never place a trade** (blocks trading
   completely, but only for that specific account state — confirmed the
   real device's account currently has trade history, so this wasn't
   actively blocking *this* deployment, but would silently and permanently
   block any account reset/switch). `estimate_value_per_point_per_lot()`
   (`risk/risk_manager.py`) returns `None` by design when there are no
   historical closed deals for the traded symbol, and
   `_execute_trade()` unconditionally aborted whenever that happened, with
   no fallback anywhere in the codebase (confirmed no alternate
   point-value field exists on `get_symbol_details`). **Fixed**: opt-in
   `risk.value_per_point_per_lot_fallback` config key.
3. **A dropped/failed MCP connection crashed the whole process** (blocks
   trading until a human notices and restarts it — worse under Docker
   before fix #1, since nothing would even restart the container's `CMD`
   loop was already inside the container's own process, `restart:
   unless-stopped` would eventually recover it, but with a full reconnect
   delay and log noise each time). `run_live()`'s `async with
   CTraderMCPClient(...)` sat outside the per-cycle try/except; only
   `KeyboardInterrupt` was caught in `main()`. **Fixed**: connection now
   inside its own retry loop.
4. **The daily-loss circuit breaker was dead** (does not itself block
   trading — the opposite risk: it silently disables a safety net rather
   than preventing trades, so a losing day would never auto-halt new
   entries the way `config.yaml`'s `risk.max_daily_loss_pct` implies it
   should). Confirmed via grep: `start_new_session()` is called from
   `backtest/engine.py`'s `run_backtest()` loop but nowhere in
   `execution/live_runner.py`. **Fixed**: `run_one_cycle()` starts a new
   session on every `session_date` rollover.
5. **`config.yaml`'s `execution.dry_run_default` was dead config** (does
   not block trading — currently the opposite: since it was never read,
   the *actual* default was always live orders, matching what
   `config.yaml` said it should be `true`/dry-run-by-default. Because
   fixing this naively — making the config value authoritative — would
   have flipped a currently-live deployment to dry-run-by-default with no
   code change on the user's part, this was fixed conservatively:
   wired up in code, but the shipped `config.yaml` value was changed to
   `false` so no existing no-flags deployment's behavior changes; a human
   now has to deliberately edit `config.yaml` (or pass `--dry-run`) to get
   dry-run-by-default.

### 17.2 — Checked and confirmed NOT a blocker

- `strategy/strategies.py`'s `"balanced"` strategy (the one currently
  active in the real device's `data/cache/.auto_control.json`, confirmed
  live: `{"enabled": true, "strategy": "balanced", "use_trained": true}`)
  enables all four signal families (`gap_fill`, `range_fade`, `breakout`,
  `trend_pullback`) — i.e. `evaluate_bar()`'s full signal set, unfiltered.
  Not an overly-restrictive gate.
- `data/cache/.kill_switch` does not exist on the real device — trading is
  not currently killed.
- `data/cache/.manual_trade_request.json` has no pending request.
- `create_kill_switch()` is defined but never called anywhere (dead code —
  meant for a human to `touch` the file directly). This is a missing
  *safety feature* (no dashboard kill-switch button), not something that
  stops automated trading, so left out of this batch. Worth a future
  dashboard "emergency stop" button if wanted.
- `config/config.yaml`'s live thresholds
  (`signals.level_proximity_atr_mult`, `breakout_confirm_atr_mult`, etc.)
  are the same defaults already exercised by this project's existing
  backtest/simulation test coverage — not unusually strict.

### 17.3 — Correction to earlier guidance in this session

An earlier turn in this session told the user to "rebuild your `api`/
`live_runner` containers" when discussing a Docker deployment. That was
inaccurate — no `live_runner` container existed in `docker-compose.yml`
before this batch (§17.1 #1). Recorded here since that earlier statement
was never corrected in the conversation it was made in.

### 17.4 — Deliberately deferred (not in this batch)

A dashboard-driven kill switch (a button that calls `create_kill_switch()`
via a new API endpoint) would close the loop on `execution/live_runner.py`'s
existing kill-switch file convention, but it's a new safety *feature*, not
a fix for something currently broken — out of scope for "fix everything
which stops it from automatically trading". Flagged here as a natural
follow-up if wanted.

## 18. Follow-up: "it is not trading yet and i am not sure if the training works" (2026-08-20 — implemented, see v0.7.4 changelog above)

Direct follow-up to §17's fixes. This section records what was actually
checked on the real device and the training bug found, so the reasoning
doesn't have to be reconstructed later.

### 18.1 — Real-device diagnosis: nothing is currently running

Checked live via the device bridge (`$HOME/mnt/58-cTraderAnthropicBot` on
the real machine):

- `ps aux | grep -iE "run_live|live_runner|uvicorn|http.server"` — **no
  matches**. Neither the dashboard API, the static dashboard server, nor
  `execution/live_runner.py` is running.
- `docker` binary not found in the device-bridge shell (this may just be
  that sandbox's `PATH`, not proof Docker isn't installed on the real
  machine — not fully conclusive either way).
- `data/cache/.auto_control.json` = `{"enabled": true, "strategy":
  "balanced", "use_trained": true}` — auto-mode *is* configured to trade;
  there's simply no running process to act on it.
- `trade_journal.sqlite3` last modified **2026-08-19 23:22** — roughly 7+
  hours before this check. Combined with the git log
  (`8b06983 fixes`, `0e10b2d ui update`, ...) and `data/reports/`showing
  optimizer runs as recent as **2026-08-20 05:38**, something was clearly
  running last night and this morning but nothing is running *right now*.

**This is very likely the actual, immediate reason "it is not trading
yet"** — independent of any of the v0.7.3 correctness fixes, which only
matter once *something* is running. The new "Live status" dashboard panel
(§18.3) makes this directly visible going forward instead of requiring
another device-bridge investigation: it says "no data yet" whenever no
live runner has reported a cycle.

Not resolved by this batch (needs a human decision, not a code fix): start
`execution/live_runner.py` — either `docker compose up -d --build` (now
that the `live_runner` service exists, §17) or
`.venv/bin/python scripts/run_live.py` in a terminal that stays open. If
using Docker, `host.docker.internal:9876` requires the cTrader desktop app
running on the same host.

### 18.2 — Training bug: optimizer always saved null best_params

Found while reading `data/reports/parameter_registry.json` on the real
device to sanity-check "not sure if the training works":

```json
"best_params": {
  "level_proximity_atr_mult": null, "breakout_confirm_atr_mult": null,
  "trend_direction_lookback": null, "risk_per_trade_pct": null,
  "min_stop_atr_mult": null
},
"performance": {
  "total_return_pct": 106.6283, "max_drawdown_pct": 46.4131,
  "win_rate": 0.4206, "n_trades": 611
}
```

Real backtest performance, but every param `null` — meaning `optimize()`
genuinely ran and picked a winner, but the specific parameter values that
won were never actually recorded. Root cause, in
`training/optimizer.py`'s `_run_backtest_sync()`:

```python
return {
    "n_trades": n_trades, "win_rate": ..., "avg_r": ..., ...,
    "params": params,   # <- nested dict, not flat keys
}
```

`optimize()` then does `df = pd.DataFrame(results)` — a list of these
dicts. Pandas gives that a single `"params"` *column* holding dict objects,
not columns named `level_proximity_atr_mult`/`breakout_confirm_atr_mult`/
etc. `api/dashboard_api.py`'s `_run_training_job()` then does:

```python
row = top_df.iloc[0].to_dict()
params = {k: row.get(k) for k in ("level_proximity_atr_mult", ...)}
```

— which only ever looks for *top-level* columns, never inside the nested
dict, so every value comes back `None`. The job still reports
`status: "completed"` and logs `"optimize done — best composite=..."`,
because from `optimize()`'s point of view nothing failed — it's a pure
shape mismatch at the DataFrame boundary, invisible without printing the
DataFrame's actual columns.

**Consequence**: `_apply_trained_params()` in `execution/live_runner.py`
guards every override with `if src_key in best and best[src_key] is not
None`, so with `best_params` all-null, `--use-trained-params` and the
dashboard's `"use_trained": true` toggle (which **is** set on the real
device) have always silently applied nothing — falling back to
`config.yaml`'s defaults with no error, no log line, nothing to indicate
the toggle isn't doing what it says.

Confirmed via `training/retrain.py` that this bug is scoped to
`optimizer.py`/`optimize()` only — `retrain.py`'s `retrain()` builds and
saves `best_combo` (a real flat dict) directly, never going through
`_run_backtest_sync()`'s return shape, so incremental retraining was never
affected.

**Fix**: `_run_backtest_sync()` now spreads `**params` directly into its
returned dict instead of nesting it. `dashboard_api.py`'s optimize-result
save path also now raises (job reported as `"failed"`, not silently
`"completed"`) if every extracted param is still `None`, so this exact
failure class can't recur silently even if the DataFrame shape changes
again in the future. `tests/test_optimizer.py` (new — this module had zero
test coverage before) asserts the flattened shape directly, both at the
single-`_run_backtest_sync()`-call level and at the
`pd.DataFrame(results)` level `optimize()` actually builds.

**Not fixed automatically**: the real device's existing
`parameter_registry.json` still has the corrupted all-null entry from past
runs — re-running "optimize" from the dashboard (or
`scripts/run_training.py optimize`) overwrites it with real values; see the
new README note under Training.

### 18.3 — New: "Live status" panel + dashboard kill switch

Two small, additive dashboard features, both aimed at "not sure if/why
it's trading" going forward rather than requiring a device-bridge
investigation each time:

- `execution/live_runner.py`'s `_write_cycle_status()` writes a snapshot to
  `data/cache/.last_cycle_status.json` after every decision point in
  `run_one_cycle()`/`_execute_trade()` (kill switch, no data from MCP, no
  signal, auto-mode disabled, strategy filtered, no vpp, sizing failed, dry
  run, order placed). `GET /api/live-status` (dashboard_api.py) reads it
  back, best-effort, same file-based-IPC convention as the kill switch /
  auto-control / manual-trade-request channels. New "Live status" sidebar
  panel shows the outcome, a human-readable detail line, and how long ago
  it happened — including explicitly saying "no data yet ... the live
  runner process may not be running" when the file has never been written,
  which is exactly what §18.1 found on the real device right now.
- `GET /api/kill-switch` / `POST /api/kill-switch/set {"active": bool}` —
  closes §17.4's deferred gap. Same file convention
  (`data/cache/.kill_switch`) `create_kill_switch()`/`check_kill_switch()`
  already used; now toggleable from the same panel instead of only via a
  human `touch`ing the file on disk.

Both are additive and read/write only file-based state that already
existed in the architecture — no change to the trading decision path
itself.