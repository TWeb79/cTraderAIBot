# ctrader-bot

Systematic US500 trading bot: market-regime detection (RANGE / BREAKOUT /
TREND) combined with volume-profile levels (POC, VAH/VAL, prior-day close as
a "magnet" level, NY-open gap-fill logic), executed via the local **cTrader
MCP server**.

The live decision path is 100% deterministic Python — no LLM in the trading
loop. The Anthropic API is used only offline, in `scripts/run_journal_review.py`,
to summarize the trade journal into written analysis.

## Version

**0.1.0** — Built 2026-08-19

## Safety

- **This system is configured to trade the confirmed DEMO account
  (48131263 / login 4262699) only.** `execution/live_runner.py` refuses to
  run against any other account, and refuses to run at all unless
  `DEMO_MODE=true` in `.env`.
- Every position requires a stop-loss. Hard daily-loss circuit breaker halts
  new entries once triggered. See `config/config.yaml` under `risk:`.
- No returns are guaranteed. Run the backtest and review the daily-return
  distribution and max drawdown before ever running the live runner, even in
  dry-run mode.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in ANTHROPIC_API_KEY
```

The cTrader MCP server must be running locally at `http://127.0.0.1:9876/mcp/`
(already registered in `.mcp.json` for Claude Code; the bot connects to it
independently via `mcp_client.py`).

## Step 0 — discover the MCP server's actual tools

The exact tool names/schemas the cTrader MCP server exposes were not known at
write time. Before trusting `mcp_client.py`, run:

```bash
python scripts/discover_mcp_tools.py
```

and reconcile its output against `src/ctrader_bot/mcp_client.py` — adjust
tool names/argument shapes there if they don't match.

## Training

Two offline training mechanisms are available for strategy analysis and parameter optimization. Neither touches the live trading loop.

```bash
# Parameter grid search optimizer
.venv/bin/python scripts/run_training.py optimize --days 60 --symbol US500

# Simulated trading engine with failure analysis
.venv/bin/python scripts/run_training.py simulate --days 60 --analyze-failures
```

Outputs are written to `data/reports/`.

## Usage

```bash
.venv/bin/python scripts/run_backtest.py                 # backtest over historical data
.venv/bin/python scripts/run_live.py --dry-run            # log-only, no orders placed
.venv/bin/python scripts/run_live.py                      # places real orders on the demo account
.venv/bin/python scripts/run_journal_review.py            # offline Anthropic digest
pytest                                                     # unit tests
```

## Dashboard

The dashboard serves on **port 8058** and the API on **port 8158**. When
accessed from a non-localhost hostname (e.g. over the LAN), the frontend
auto-detects the API port as `<dashboard port> + 100`, so no config change is
needed to reach it from another machine.

```bash
# Terminal 1 — API
uvicorn api.dashboard_api:app --host 0.0.0.0 --port 8158

# Terminal 2 — static files
python -m http.server 8058 --directory dashboard
```

Then open `http://localhost:8058`.

### Panels

- **Chart** — candlestick or tick-volume "orderflow" view (toggle in the
  toolbar), EMA/POC/value-area overlay, prediction TP/ENTRY/SL lines, session
  markers (Asia/Frankfurt/NY), and mouse wheel zoom + drag pan (double-click
  to reset). The days-of-history selector (1d/3d/7d/14d) re-fetches from
  `/api/bars`.
- **Session levels** — a live UTC session clock plus the "core datapoints"
  readout: prior session POC/VAH/VAL, the pre-NY/NY sub-session split, prior
  day close, and prior NY open (see `strategy/levels.py`).
- **Auto trading** — lets you gate the live runner to a specific named
  strategy (or turn automated entries off entirely) without restarting
  `run_live.py`; see "Auto-mode gating" below. Also shows a model-learning
  gauge and sparkline driven by the deterministic parameter registry's
  optimization history (`training/registry.py`) — there is **no neural
  network** behind this; it's a statistics readout, consistent with this
  project's no-ML-in-the-live-loop design.
- **Training** — starts an `optimize` or `simulate` job from the dashboard
  (same jobs as `scripts/run_training.py`), streams progress over the
  WebSocket, and can chain "optimize, then simulate" with one click.
- **Signal feed / Open position / Trade journal** — unchanged from earlier
  versions.

### API endpoints

In addition to `/api/health`, `/api/version`, `/api/state`, `/api/journal`,
`/api/digest`, `/api/strategies`, `/api/sessions`, `/api/registry`, and
`/api/analysis`:

- `GET /api/registry/history?limit=20` — optimizer/retrain run history plus
  live-feedback summary and current performance, for the learning sparkline.
- `GET /api/bars?days=3&timeframe=M5` — enriched bars (indicators, session
  levels, regime) plus `session_markers` for the chart.
- `GET /api/auto` / `POST /api/auto/set` — read/write the dashboard's
  auto-mode state (`enabled`, `strategy`, `use_trained`). Writing also
  updates the `data/cache/.auto_control.json` file the live runner reads
  (see below) and broadcasts the new state over the WebSocket.
- `POST /api/training` / `GET /api/training` — start and poll a background
  training job (`optimize` or `simulate`); progress is also broadcast over
  the WebSocket as `{"type": "training", ...}`.

### Auto-mode gating (dashboard ↔ live runner)

The dashboard and `execution/live_runner.py` run as **separate processes**
and never share memory, so the "Enable auto mode" toggle communicates
through a small JSON control file, `data/cache/.auto_control.json`, the same
pattern already used by the kill switch. `POST /api/auto/set` writes it;
`live_runner.load_auto_control()` reads it once per cycle. If the file is
missing, unreadable, or malformed, the live runner behaves exactly as before
this feature existed (every risk-approved signal is taken) — the dashboard
is opt-in and its absence never silently pauses live trading.

## Architecture

See `ARCHITECTURE.md` for the full system design, module responsibilities,
and data flow.
# cTraderAIBot
