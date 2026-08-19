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

The dashboard serves on **port 8058** and the API on **port 8158**.

```bash
# Terminal 1 — API
uvicorn api.dashboard_api:app --host 0.0.0.0 --port 8158

# Terminal 2 — static files
python -m http.server 8058 --directory dashboard
```

Then open `http://localhost:8058`.

## Architecture

See `ARCHITECTURE.md` for the full system design, module responsibilities,
and data flow.
# cTraderAIBot
