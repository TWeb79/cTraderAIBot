# cTrader Local MCP Server — Cheatsheet

Your config points at `http://127.0.0.1:9876/mcp/` — this is the **official Spotware cTrader Local MCP server**, built into cTrader Windows/Mac itself (Settings → MCP Server). It's not a separate open-source project; it's a first-party feature of the desktop app.

## How it works (important)

- **Requires cTrader Windows or Mac to be running and logged in.** The MCP server authenticates via your active desktop session — if the app isn't open, your AI client will fail to connect.
- **Enable it first:** Settings → MCP Server → tick "Enable MCP server". There's also a separate "Allow trading via MCP" toggle and an optional "Require confirmation for trading operations" toggle.
- **Pull-based, not streaming.** Every tool is request/response — the agent calls a tool, gets an answer, done. There's no WebSocket-style push feed. "Real-time" means *the current live value, fetched on demand*, not a continuously updating subscription. To watch something over time, the agent (or you) has to keep re-asking.
- **It can place real trades.** If trading is enabled, the agent can execute real orders on your live/demo account. Use the confirmation toggle if you want a manual check before each trade.

## Tool categories

| Category | What it covers |
|---|---|
| **Trading** | Market/limit/stop/stop-limit orders, SL/TP, labels/comments, view/modify/close positions (full, partial, conditional close), view/amend/cancel pending orders |
| **Account** | List accounts, balance/equity/free margin, win rate & profit factor, available symbols, trading session hours, deposit currency, margin-level checks |
| **Charts** | Open/close/focus charts, switch symbol or period, scroll/zoom, read viewport, add/edit/delete chart objects (trendlines, rectangles, Fib retracements, arrows), apply/save/delete templates |
| **Analysis (market data)** | Live bid/ask, historical candles, spread, session high/low, market news, deal/order history, portfolio exposure, risk checks |
| **Indicators** | Add/remove/list built-in indicators (RSI, SMA, MACD, etc.), tune parameters, read computed values off the active chart |
| **Plugins** | List, start, stop desktop plugins |
| **UI layout** | Workspaces, Market Watch, watchlists, Active Symbol Panel, Trade Watch, notifications |
| **Price alerts** | Create, view, delete price alerts |

## Your question: real-time bars & volume

**Bars/candles — yes, on demand:**
- Periods available: `m1, m5, m15, m30, h1, h4, d1, w1, mn1`
- Example prompt: *"Get the last 24 hours of hourly candles for EURUSD"* or *"Get 100 five-minute candles for XAUUSD."*
- **Cap: 1,000 bars per request.** For longer history the agent automatically pages across multiple calls.
- There's also a dedicated live-price tool: *"What are the current bid and ask prices for EURUSD?"* — works for single or multiple symbols in one prompt.

**Volume — likely yes, but confirm it yourself:**
cTrader's underlying bar data (via its Open API) has always carried a tick-volume field alongside OHLC, so the MCP's candle tool almost certainly returns it too. The official MCP docs, however, only describe *prompt examples*, not literal field schemas — they never explicitly say "volume is included." So don't take my word for it: ask something like *"Get the last 10 H1 candles for EURUSD and show me the volume for each."* If the field isn't there, the docs don't expose a separate volume-only tool as a fallback.

**"Real-time" caveat:** since there's no streaming/subscription mechanism, a live bar-by-bar feed isn't possible through MCP alone. What you *can* do is have the agent poll — e.g. re-run the candle/price prompt on a loop or on your trigger — but each call is a fresh pull, not a push update.

## Useful example prompts

```
Using the cTrader local MCP server...

# Market data
What are the current bid and ask prices for EURUSD, GBPUSD and USDJPY?
Get the last 24 hours of hourly candles for EURUSD.
What is the current spread on EURUSD?

# Account
Give me a summary of my account: balance, equity, free margin, open positions.
Check my free margin as a percentage of equity, warn me if below 20%.

# Trading
Buy 1 lot of EURUSD at market with stop loss at 1.1150 and take profit at 1.1300.
Close all positions losing more than $500.

# Charts + indicators
Open a chart for EURUSD on H4, add RSI and a 200-period SMA, tell me if RSI is overbought.
```

Tip from the docs: start a new session with **"Using the cTrader local MCP server..."** — otherwise some AI clients default to web search instead of calling the tool.

## Gotchas

- **App must be running before your AI client starts** — otherwise the connection fails outright.
- **No financial advice** — Spotware is explicit that outputs aren't investment/legal/tax advice; you're responsible for supervising anything the agent does.
- **Local vs Remote MCP:** this is the *Local* server (fuller feature set: charts, indicators, plugins, UI, alerts — needs desktop app). There's also a *Remote* MCP server for cTrader Web with a narrower scope (trading, account, market data only) if you ever need browser-based access instead.

Source: [help.ctrader.com/ctrader-ai-agent-connect](https://help.ctrader.com/ctrader-ai-agent-connect/)