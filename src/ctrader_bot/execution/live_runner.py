"""Live trading runner: deterministic loop over the cTrader local MCP server.

This is the main entry point for live/demo trading. It:

1. Connects to the cTrader MCP server via ``mcp_client.CTraderMCPClient``.
2. Fetches market data + account state each cycle.
3. Enriches bars with regime + volume-profile levels using
   ``backtest.engine.prepare_backtest_bars``.
4. Evaluates the deterministic strategy (``strategy.signals.evaluate_bar``).
5. Passes the resulting decision through the hard risk gate
   (``risk.risk_manager.RiskManager``).
6. Places the trade if approved.
7. Polls until the position closes, then records a structured reflection
   in the SQLite journal.

The LLM is **not** in the trading loop. It is used only for the optional
offline journal digest in ``scripts/run_journal_review.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


# ── Config ────────────────────────────────────────────────────────────────

def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_settings() -> dict[str, Any]:
    with open(_project_root() / "config" / "config.yaml") as f:
        return yaml.safe_load(f)


def load_secrets() -> dict[str, str | bool]:
    load_dotenv(_project_root() / ".env")
    return {
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY") or None,
        "ctrader_account_id": os.environ.get("CTRADER_ACCOUNT_ID", ""),
        "ctrader_login": os.environ.get("CTRADER_LOGIN", ""),
        "demo_mode": os.environ.get("DEMO_MODE", "true").strip().lower() in ("1", "true", "yes"),
        "ctrader_mcp_url": os.environ.get("CTRADER_MCP_URL", "http://127.0.0.1:9876/mcp/"),
    }


SETTINGS = load_settings()
SECRETS = load_secrets()

SYMBOL = SETTINGS.get("symbol", "US500")
TIMEFRAME = SETTINGS.get("timeframes", {}).get("signal", "M5")
PROFILE_TIMEFRAME = SETTINGS.get("timeframes", {}).get("profile", "M1")
BARS_FOR_CONTEXT = SETTINGS.get("execution", {}).get("bars_for_context", 100)
POLL_SECONDS = SETTINGS.get("execution", {}).get("poll_interval_seconds", 15)
KILL_SWITCH_PATH = str(_project_root() / "data" / "cache" / ".kill_switch")


# ── Schemas ────────────────────────────────────────────────────────────────

class TradeDecision(BaseModel):
    action: str
    confidence: float
    entry_type: str
    entry_price: float | None = None
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    reasoning: str
    invalidation_condition: str


class TradeReflection(BaseModel):
    outcome: str
    r_multiple: float
    what_matched_expectation: str
    what_diverged: str
    lesson: str
    setup_tag: str
    pnl: float = 0.0


# ── MCP client wrapper ────────────────────────────────────────────────────

from ctrader_bot.mcp_client import CTraderMCPClient


# ── Journal ───────────────────────────────────────────────────────────────

from ctrader_bot.journal.store import Journal


DB_PATH = str(_project_root() / "trade_journal.sqlite3")


# ── Strategy / Risk imports ───────────────────────────────────────────────

from ctrader_bot.backtest.engine import prepare_backtest_bars
from ctrader_bot.indicators.regime import adx_di, classify_regime
from ctrader_bot.risk.risk_manager import (
    RiskManager, RiskLimits, compute_stop_distance, estimate_value_per_point_per_lot,
    fixed_rr_target_price, margin_based_volume, trailing_stop_update,
)
from ctrader_bot.strategy.levels import (
    attach_prior_session_levels,
    compute_ny_open_gap_state,
    compute_session_levels,
)
from ctrader_bot.strategy.signals import evaluate_bar, Side, Signal
from ctrader_bot.strategy.strategies import get_strategy
from ctrader_bot.training.registry import ParameterRegistry


# ── Kill switch ───────────────────────────────────────────────────────────

def check_kill_switch() -> bool:
    """Returns True if trading should continue."""
    return not Path(KILL_SWITCH_PATH).exists()


def create_kill_switch() -> None:
    Path(KILL_SWITCH_PATH).touch()


def remove_kill_switch() -> None:
    p = Path(KILL_SWITCH_PATH)
    if p.exists():
        p.unlink()


# ── Dashboard auto-mode control (file-based IPC) ────────────────────────────
#
# The dashboard API (api/dashboard_api.py, a separate process) writes this
# file from POST /api/auto/set. It is read fresh every cycle so a toggle in
# the dashboard takes effect on the next cycle without restarting the live
# runner. This is advisory, additive gating only:
#   - a missing/unreadable file, or a file without "enabled", means
#     "unchanged" — the live runner behaves exactly as it always has
#     (every risk-approved signal is taken), so CLI-only usage with no
#     dashboard running is unaffected.
#   - {"enabled": false} pauses new entries (existing open positions are
#     still managed/closed normally).
#   - {"enabled": true, "strategy": "<name>"} additionally restricts entries
#     to signal families the named strategy (strategy/strategies.py) opts
#     into, so "switching on auto mode" with a strategy selected in the
#     dashboard actually changes what the live loop trades, not just what
#     the analysis panel predicts.
AUTO_CONTROL_PATH = str(_project_root() / "data" / "cache" / ".auto_control.json")


def load_auto_control() -> dict[str, Any]:
    """Best-effort read of the dashboard's auto-mode control file. Never raises —
    a missing or malformed file is treated as 'no override'."""
    try:
        with open(AUTO_CONTROL_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


# ── Manual trade requests (one-click "execute predicted trade" button) ─────
#
# The dashboard's prediction panel writes this file when the user clicks the
# single OK button — implementationplan.md's "manual execute" feature. It is
# picked up here (not executed directly by the dashboard API process) so a
# manual trade goes through the exact same risk-sized, kill-switch-respecting,
# journal-tracked order pipeline as automated trades, via the one process
# that's the source of truth for open positions/risk. See _execute_trade().
MANUAL_TRADE_REQUEST_PATH = str(_project_root() / "data" / "cache" / ".manual_trade_request.json")


def consume_manual_trade_request() -> dict[str, Any] | None:
    """Read-and-delete the pending manual trade request, if any. Deleting
    before execution (rather than after) means a request is attempted at
    most once even if this process crashes mid-trade — consistent with this
    module's other file-based IPC (kill switch, auto control) preferring a
    missing/malformed file to degrade to 'do nothing' rather than repeat an
    action. Returns None if there is no valid pending request."""
    try:
        with open(MANUAL_TRADE_REQUEST_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    try:
        Path(MANUAL_TRADE_REQUEST_PATH).unlink()
    except OSError:
        pass
    if not isinstance(data, dict):
        return None
    if data.get("direction") not in ("LONG", "SHORT"):
        return None
    for key in ("entry", "stop", "target"):
        if not isinstance(data.get(key), (int, float)):
            return None
    return data


# ── Cycle diagnostics ("why isn't it trading?") ─────────────────────────────
#
# Every early-return / decision point in run_one_cycle() and _execute_trade()
# used to only print() to stdout — invisible unless a human is tailing the
# process's own console/container logs. This file-based snapshot (same
# convention as the kill switch / auto-control / manual-trade-request IPC
# above) is read by GET /api/live-status so the dashboard can show *why*
# nothing has traded yet without that. Best-effort only: a failed write here
# must never interrupt trading.
LAST_CYCLE_STATUS_PATH = str(_project_root() / "data" / "cache" / ".last_cycle_status.json")


def _write_cycle_status(outcome: str, detail: str = "", **extra: Any) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,
        "detail": detail,
        **extra,
    }
    try:
        Path(LAST_CYCLE_STATUS_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(LAST_CYCLE_STATUS_PATH).write_text(json.dumps(payload, default=str))
    except OSError as e:
        print(f"[warn] failed to write cycle status: {e}")


# ── Helpers ───────────────────────────────────────────────────────────────

def _bars_to_df(bars: list[Any]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "timestamp": b.timestamp,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in bars
    ])


def _build_decision(side: Side, entry_price: float, stop_price: float,
                    target_price: float, reason: str) -> TradeDecision:
    rr = 0.0
    if stop_price != entry_price:
        rr = abs(target_price - entry_price) / abs(stop_price - entry_price)
    return TradeDecision(
        action=side.value,
        confidence=0.0,
        entry_type="market",
        entry_price=entry_price,
        stop_loss=stop_price,
        take_profit=target_price,
        risk_reward_ratio=rr,
        reasoning=reason,
        invalidation_condition="stop hit or opposite signal generated",
    )


def _build_reflection(pnl: float, risk_amount: float, reason: str) -> TradeReflection:
    r_multiple = 0.0
    if risk_amount > 0:
        r_multiple = pnl / risk_amount
    outcome = "WIN" if r_multiple > 0 else "LOSS" if r_multiple < 0 else "BREAKEVEN"
    return TradeReflection(
        outcome=outcome,
        r_multiple=r_multiple,
        what_matched_expectation="",
        what_diverged="",
        lesson=f"Closed as {outcome} on {reason}",
        setup_tag=reason,
        pnl=pnl,
    )


def _spot_price_for_side(spot: dict, side: Side) -> float | None:
    """get_spot_prices' response schema isn't documented beyond bid/ask-style
    fields (same undocumented-casing situation as get_positions — see
    dashboard/js/app.js's normalizePositions()), so this defensively tries
    the common variants. Uses bid for a long's profit/TP-distance check (a
    long closes at bid) and ask for a short (a short closes at ask); falls
    back to a generic mid/price/close/last field if bid/ask aren't present.
    Returns None (never guesses) if nothing usable is found — trailing-stop
    logic must then skip this poll rather than act on a fabricated price.
    """
    if not isinstance(spot, dict):
        return None
    bid, ask = spot.get("bid"), spot.get("ask")
    if bid is not None and ask is not None:
        return float(bid) if side == Side.BUY else float(ask)
    for key in ("price", "mid", "close", "last"):
        if spot.get(key) is not None:
            return float(spot[key])
    return None


def _risk_limits_from_settings(settings: dict[str, Any]) -> RiskLimits:
    r = settings["risk"]
    return RiskLimits(
        risk_per_trade_pct=r["risk_per_trade_pct"],
        max_daily_loss_pct=r["max_daily_loss_pct"],
        max_open_risk_pct=r.get("max_open_risk_pct", 10.0),
        min_stop_atr_mult=r.get("min_stop_atr_mult", 0.5),
    )


# ── Trade execution (shared by the automated signal path and the manual
#    "execute predicted trade" dashboard button) ────────────────────────────

async def _execute_trade(
    mcp: CTraderMCPClient, journal: Journal, risk_manager: RiskManager,
    symbol: str, side: Side, entry_price: float, raw_stop_price: float,
    target_price: float | None, reason: str, regime: str, atr_val: float | None,
    settings: dict[str, Any], symbol_details: dict[str, Any],
    dry_run: bool, registry: ParameterRegistry | None,
) -> None:
    """Size, place, track-to-close, and journal a single trade.

    Both the automated signal path (run_one_cycle, via evaluate_bar) and the
    manual dashboard "execute predicted trade" path (consume_manual_trade_request)
    call this, so a manually-triggered trade gets exactly the same risk-per-trade
    sizing, daily-loss/open-risk gate, min-stop-ATR floor, position tracking, and
    journal/registry-feedback recording as an automated one — only the source of
    side/entry/stop/target differs.
    """
    account_raw = await mcp.get_balance()
    equity = account_raw.get("equity", account_raw.get("balance", 0))

    deals = await mcp.get_deals(symbol=symbol, count=200)
    vpp = estimate_value_per_point_per_lot(deals, symbol)
    if vpp is None:
        # No historical closed deals for `symbol` on this account (e.g. a
        # fresh/reset demo account) — estimate_value_per_point_per_lot
        # refuses to guess by design. risk.value_per_point_per_lot_fallback
        # (config.yaml, default null/unset) is an explicit opt-in escape
        # hatch so that case doesn't permanently block every trade attempt;
        # it is never used once real deal history exists.
        fallback = settings.get("risk", {}).get("value_per_point_per_lot_fallback")
        if fallback is None:
            msg = (f"cannot estimate value_per_point_per_lot (no historical deals for "
                   f"{symbol} and no risk.value_per_point_per_lot_fallback configured)")
            print(f"[{reason}] {msg}")
            _write_cycle_status("no_vpp", msg, signal_reason=reason, regime=regime)
            return
        vpp = float(fallback)
        print(f"[{reason}] no historical deals for {symbol}; using configured "
              f"risk.value_per_point_per_lot_fallback={vpp} (explicit fallback, not empirically derived)")

    raw_stop_distance = abs(entry_price - raw_stop_price)
    stop_distance = compute_stop_distance(atr_val or 0.0, raw_stop_distance, settings["risk"]["min_stop_atr_mult"])

    if side == Side.BUY:
        stop_price = entry_price - stop_distance
    else:
        stop_price = entry_price + stop_distance

    # Fixed reward:risk override (§15.7) — opt-in via risk.enforce_fixed_rr.
    # Recomputes the take-profit from the (post-floor) stop distance instead
    # of trusting the signal's own target_price. Off by default.
    if settings.get("risk", {}).get("enforce_fixed_rr", False):
        target_rr_ratio = float(settings["risk"].get("target_rr_ratio", 3.0))
        target_price = fixed_rr_target_price(entry_price, stop_price, target_rr_ratio)

    volume = risk_manager.size_trade(
        equity=equity,
        entry_price=entry_price,
        stop_price=stop_price,
        value_per_point_per_lot=vpp,
        min_volume=symbol_details["minVolume"],
        max_volume=symbol_details["maxVolume"],
        volume_step=symbol_details["volumeStep"],
    )

    if volume is None:
        msg = "trade not sized (daily-loss halt, open-risk budget, or below min volume)"
        print(f"[{reason}] {msg}")
        _write_cycle_status("sizing_failed", msg, signal_reason=reason, regime=regime)
        return

    # Margin-%-of-free-margin sizing (§15.6) — opt-in via
    # risk.position_sizing_mode: "margin_pct". Acts only as a further cap on
    # the risk_pct-sized volume above, never as a way to size larger than
    # risk_per_trade_pct already allows.
    if settings.get("risk", {}).get("position_sizing_mode") == "margin_pct":
        free_margin = account_raw.get("freeMargin", account_raw.get("free_margin"))
        margin_pct = settings["risk"].get("margin_pct_of_free_margin", 5.0)
        try:
            margin_quote = await mcp.calculate_margin(symbol, 1.0, "lots")
            margin_per_lot = margin_quote.get("margin") if isinstance(margin_quote, dict) else None
        except Exception as e:
            print(f"[{reason}] margin_pct sizing: calculate_margin failed ({e}); using risk_pct volume only")
            margin_per_lot = None
        margin_volume = margin_based_volume(
            free_margin=free_margin, margin_pct=margin_pct, margin_per_lot=margin_per_lot,
            min_volume=symbol_details["minVolume"], max_volume=symbol_details["maxVolume"],
            volume_step=symbol_details["volumeStep"],
        )
        if margin_volume is not None:
            volume = min(volume, margin_volume)

    risk_amount = stop_distance * volume * vpp

    order_side = "buy" if side == Side.BUY else "sell"
    order_args: dict[str, Any] = {
        "symbolName": symbol,
        "side": order_side,
        "volume": volume,
        "volumeType": "lots",
    }

    pip_size = symbol_details.get("pipSize", 0.0001)
    stop_pips = abs(stop_price - entry_price) / pip_size
    order_args["stopLossPips"] = stop_pips

    if target_price:
        tp_pips = abs(target_price - entry_price) / pip_size
        order_args["takeProfitPips"] = tp_pips

    if dry_run:
        print(f"[dry-run] {reason} side={order_side} volume={volume} stop_pips={stop_pips:.1f}")
        _write_cycle_status("dry_run", f"would place {order_side} volume={volume} stop_pips={stop_pips:.1f}",
                            signal_reason=reason, regime=regime, side=order_side, volume=volume)
        return

    placed = await mcp.place_market_order(**order_args)
    opened_at = datetime.now(timezone.utc).isoformat()
    print(f"[order] placed ({reason}): {placed}")

    position_id = placed.get("positionId") or placed.get("position_id")
    if not position_id:
        print("[order] no position id returned")
        _write_cycle_status("order_placed_no_id", "order accepted but MCP returned no position id",
                            signal_reason=reason, regime=regime, side=order_side, volume=volume)
        return

    trade_id = str(position_id)
    risk_manager.register_open_trade(trade_id, risk_amount)

    # Written now (order confirmed, not after the poll-until-close loop
    # below, which can run for hours) — this is what "why isn't it trading"
    # diagnostics actually needs to answer: was an order placed this cycle.
    _write_cycle_status("order_placed", f"{order_side} {volume} lots, position {trade_id}",
                        signal_reason=reason, regime=regime, side=order_side, volume=volume,
                        position_id=trade_id)

    trailing_cfg = settings.get("risk", {}).get("trailing_stop", {}) or {}
    trailing_enabled = bool(trailing_cfg.get("enabled", False)) and bool(target_price)
    current_stop, current_target = stop_price, target_price

    close_info: dict[str, Any] = {}
    while True:
        await asyncio.sleep(POLL_SECONDS)
        current = await mcp.get_positions()
        still_open = any(p.get("id") == position_id or p.get("positionId") == position_id
                         for p in (current if isinstance(current, list) else current.get("positions", [])))
        if not still_open:
            deals = await mcp.get_deals(symbol=symbol, count=5)
            close_info = {"deals": deals}
            break

        if trailing_enabled:
            try:
                spot = await mcp.get_spot_prices(symbol)
                price = _spot_price_for_side(spot, side)
            except Exception as e:
                print(f"[trailing] get_spot_prices failed: {e}")
                price = None
            if price is not None:
                new_stop, new_target = trailing_stop_update(
                    is_buy=(side == Side.BUY), entry_price=entry_price, current_price=price,
                    current_stop=current_stop, current_target=current_target, pip_size=pip_size,
                    trigger_pips=float(trailing_cfg.get("trigger_pips", 3.0)),
                    lock_pips=float(trailing_cfg.get("lock_pips", 1.4)),
                    tp_extend_trigger_pips=float(trailing_cfg.get("tp_extend_trigger_pips", 5.0)),
                    tp_extend_pips=float(trailing_cfg.get("tp_extend_pips", 5.0)),
                    sl_trail_distance_pips=float(trailing_cfg.get("sl_trail_distance_pips", 3.0)),
                )
                if new_stop != current_stop or new_target != current_target:
                    try:
                        await mcp.amend_position(int(position_id), stop_loss=new_stop, take_profit=new_target)
                        current_stop, current_target = new_stop, new_target
                        print(f"[trailing] amended position {position_id}: stop={new_stop:.4f} target={new_target}")
                    except Exception as e:
                        print(f"[trailing] amend_position failed: {e}")

    pnl = 0.0
    for d in close_info.get("deals", []):
        if d.get("symbolName") == symbol:
            pnl += d.get("grossProfit", 0.0)

    risk_manager.register_closed_trade(trade_id, pnl)

    decision = _build_decision(side, entry_price, current_stop, current_target or entry_price, reason)
    reflection = _build_reflection(pnl, risk_amount, reason)
    journal.record_trade(decision, reflection, symbol, opened_at=opened_at)
    print(f"[reflection] {reflection.outcome} {reflection.r_multiple:.2f}R")

    # Write-only feedback: append the live outcome to the registry so the
    # optimizer/retrain can later incorporate real-market performance. This
    # never influences the live decision (determinism preserved).
    if registry is not None:
        registry.append_live_feedback({
            "setup_tag": reason,
            "regime": regime,
            "r_multiple": reflection.r_multiple,
            "pnl": pnl,
            "entry_price": entry_price,
            "atr": atr_val if atr_val is not None else 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


# ── Main loop ─────────────────────────────────────────────────────────────

async def run_one_cycle(mcp: CTraderMCPClient, journal: Journal,
                        risk_manager: RiskManager, symbol: str, timeframe: str,
                        profile_timeframe: str, settings: dict[str, Any],
                        dry_run: bool = False,
                        registry: ParameterRegistry | None = None) -> None:
    """Execute one deterministic decision cycle."""
    if not check_kill_switch():
        print("[kill] stop requested")
        _write_cycle_status("kill_switch", "kill switch active — cycle skipped")
        return

    from_dt = datetime.now(timezone.utc) - timedelta(days=1)
    to_dt = datetime.now(timezone.utc)

    signal_bars_raw = await mcp.get_trendbars(symbol, timeframe, from_dt, to_dt, limit=BARS_FOR_CONTEXT)
    if not signal_bars_raw:
        print("[cycle] no signal bars returned")
        _write_cycle_status("no_data", f"MCP returned no {timeframe} bars for {symbol}")
        return

    profile_bars_raw = await mcp.get_trendbars(symbol, profile_timeframe, from_dt, to_dt, limit=1000)
    if not profile_bars_raw:
        print("[cycle] no profile bars returned")
        _write_cycle_status("no_data", f"MCP returned no {profile_timeframe} bars for {symbol}")
        return

    signal_df = _bars_to_df(signal_bars_raw)
    profile_df = _bars_to_df(profile_bars_raw)

    symbol_details = await mcp.get_symbol_details(symbol)
    cfg = {
        "session_rollover_utc_hour": 21,
        "pip_size": symbol_details.get("pipSize", 1.0),
        "volume_profile": settings["volume_profile"],
        "session": settings["session"],
        "regime": settings["regime"],
        "indicators": settings.get("indicators", {}),
    }

    # Macro (M15) MACD confirmation background analysis (§15.3) — only
    # fetched when opted in, so a live runner with the flag off makes no
    # extra MCP calls versus before this feature existed. Only wired here
    # (not into the optimizer/simulator/backtest paths yet) — evaluate_bar
    # defensively no-ops the filter if the macro columns aren't present, so
    # this is safe to leave off by default.
    macro_bars_df = None
    if settings.get("signals", {}).get("require_macro_confirmation", False):
        macro_timeframe = settings.get("timeframes", {}).get("macro", "M15")
        try:
            macro_bars_raw = await mcp.get_trendbars(symbol, macro_timeframe, from_dt, to_dt, limit=BARS_FOR_CONTEXT)
            if macro_bars_raw:
                macro_bars_df = _bars_to_df(macro_bars_raw)
        except Exception as e:
            print(f"[cycle] macro bars fetch failed ({e}); proceeding without macro confirmation this cycle")

    bars = prepare_backtest_bars(signal_df, profile_df, cfg, macro_bars=macro_bars_df)

    if bars.empty or len(bars) == 0:
        print("[cycle] no enriched bars")
        _write_cycle_status("no_data", "prepare_backtest_bars returned no enriched bars")
        return

    latest = bars.iloc[-1]
    lookback = settings["signals"].get("trend_direction_lookback", 20)
    recent_closes = bars["close"].iloc[max(0, len(bars) - lookback - 1):]
    atr_val = latest.get("atr")
    regime = str(latest.get("regime", "UNKNOWN"))

    # Daily-loss circuit breaker session rollover. Without this,
    # risk_manager.day_start_equity stays 0.0 forever and
    # record_realized_pnl() can never set halted_today=True (see its
    # `if self.day_start_equity > 0` guard) — the breaker would be silently
    # dead in live trading, same as it's driven in backtest/engine.py's
    # run_backtest() loop. `latest["session_date"]` is absent when bars are
    # mocked/stubbed (tests) or the enriched-bars pipeline changes shape;
    # `.get()` degrades to None in that case and this block is a no-op, same
    # as before this fix existed.
    session_date = latest.get("session_date")
    if session_date is not None and risk_manager.current_session_date != session_date:
        account_raw = await mcp.get_balance()
        session_equity = account_raw.get("equity", account_raw.get("balance", 0))
        risk_manager.start_new_session(session_date, session_equity)
        print(f"[session] new session {session_date}: day_start_equity={session_equity}")

    # A pending manual "execute predicted trade" request from the dashboard
    # is handled independently of this cycle's automated signal — the user
    # already confirmed direction/entry/stop/target, so it doesn't need (and
    # may not match) whatever evaluate_bar() decides below.
    manual_request = consume_manual_trade_request()
    if manual_request is not None:
        manual_side = Side.BUY if manual_request["direction"] == "LONG" else Side.SELL
        print(f"[manual] executing dashboard-requested trade: {manual_request}")
        await _execute_trade(
            mcp, journal, risk_manager, symbol, manual_side,
            float(manual_request["entry"]), float(manual_request["stop"]),
            float(manual_request["target"]), manual_request.get("reason") or "manual-dashboard",
            regime, atr_val, settings, symbol_details, dry_run, registry,
        )

    signal = evaluate_bar(
        latest, recent_closes, atr_val,
        level_proximity_atr_mult=settings["signals"]["level_proximity_atr_mult"],
        breakout_confirm_atr_mult=settings["signals"]["breakout_confirm_atr_mult"],
        trend_direction_lookback=lookback,
        enable_bounce_strategies=settings["signals"].get("enable_bounce_strategies", False),
        bounce_proximity_atr_mult=settings["signals"].get("bounce_proximity_atr_mult", 0.25),
        require_macro_confirmation=settings["signals"].get("require_macro_confirmation", False),
    )

    if signal is None:
        print("[cycle] no signal")
        _write_cycle_status("no_signal", "no setup matched this bar", regime=regime)
        return

    control = load_auto_control()
    if control.get("enabled") is False:
        print(f"[cycle] auto mode disabled via dashboard — signal '{signal.reason}' not taken")
        _write_cycle_status("auto_disabled", "auto mode disabled via dashboard",
                            signal_reason=signal.reason, regime=regime)
        return
    control_strategy = control.get("strategy")
    if control_strategy:
        strat = get_strategy(control_strategy)
        if not strat.accepts(signal.reason):
            print(f"[cycle] signal '{signal.reason}' not enabled by dashboard strategy "
                  f"'{control_strategy}' — skipping")
            _write_cycle_status("strategy_filtered",
                                f"signal not enabled by dashboard strategy '{control_strategy}'",
                                signal_reason=signal.reason, regime=regime, strategy=control_strategy)
            return

    await _execute_trade(
        mcp, journal, risk_manager, symbol, signal.side, signal.entry_price,
        signal.stop_price, signal.target_price, signal.reason, regime,
        atr_val, settings, symbol_details, dry_run, registry,
    )


def _apply_trained_params(settings: dict[str, Any], registry: ParameterRegistry) -> list[str]:
    """Override signal/risk keys from the registry's best params.

    Returns a list of human-readable override descriptions for logging.
    Only non-empty numeric values are applied; config.yaml otherwise wins.
    """
    best = registry.load_best_params()
    if not best:
        return []

    signal_map = {
        "level_proximity_atr_mult": ("signals", "level_proximity_atr_mult"),
        "breakout_confirm_atr_mult": ("signals", "breakout_confirm_atr_mult"),
        "trend_direction_lookback": ("signals", "trend_direction_lookback"),
    }
    risk_map = {
        "risk_per_trade_pct": ("risk", "risk_per_trade_pct"),
        "min_stop_atr_mult": ("risk", "min_stop_atr_mult"),
    }

    overrides: list[str] = []
    for src_key, (section, dst_key) in signal_map.items():
        if src_key in best and best[src_key] is not None:
            settings.setdefault(section, {})[dst_key] = best[src_key]
            overrides.append(f"signals.{dst_key}={best[src_key]} (trained)")
    for src_key, (section, dst_key) in risk_map.items():
        if src_key in best and best[src_key] is not None:
            settings.setdefault(section, {})[dst_key] = best[src_key]
            overrides.append(f"risk.{dst_key}={best[src_key]} (trained)")
    return overrides


async def run_live(dry_run: bool = False, symbol: str | None = None,
                   use_trained_params: bool = False,
                   registry_path: str | None = None) -> None:
    """Main live trading loop."""
    symbol = symbol or SYMBOL
    settings = load_settings()
    secrets = load_secrets()
    registry = ParameterRegistry(registry_path) if registry_path else ParameterRegistry()

    if use_trained_params:
        overrides = _apply_trained_params(settings, registry)
        if overrides:
            for line in overrides:
                print(f"[trained] applied {line}")
        else:
            print("[trained] no best params in registry; using config.yaml")

    risk_limits = _risk_limits_from_settings(settings)
    risk_manager = RiskManager(limits=risk_limits)
    journal = Journal(DB_PATH)

    print(f"Running live loop for {symbol} (dry_run={dry_run}, use_trained_params={use_trained_params})")
    print("Create .kill_switch to stop gracefully.")

    # The MCP connection itself (not just each cycle) is now retried: without
    # this, a connection failure at startup — e.g. this process starting
    # before the cTrader desktop app is ready, or a transient drop of an
    # already-open connection — crashed the whole process uncaught (only
    # KeyboardInterrupt was handled in main()), and nothing here restarted
    # it. Docker's `restart: unless-stopped` (see docker-compose.yml's
    # live_runner service) is a second, coarser safety net on top of this;
    # this retry is what also protects a manually-run `python
    # scripts/run_live.py` process.
    while check_kill_switch():
        try:
            async with CTraderMCPClient(secrets["ctrader_mcp_url"]) as mcp:
                positions = await mcp.get_positions()
                journal.save_cycle_state([str(p.get("id")) for p in positions])

                if secrets.get("demo_mode"):
                    try:
                        await mcp.assert_demo_account(int(secrets.get("ctrader_account_id", 0)))
                    except Exception as e:
                        print(f"[warn] demo account check failed: {e}")

                while check_kill_switch():
                    try:
                        await run_one_cycle(
                            mcp, journal, risk_manager, symbol, TIMEFRAME,
                            PROFILE_TIMEFRAME, settings, dry_run=dry_run,
                            registry=registry,
                        )
                    except Exception as e:
                        print(f"[error] cycle failed: {e}")
                    await asyncio.sleep(POLL_SECONDS)
        except Exception as e:
            print(f"[error] MCP connection failed or dropped: {e}; retrying in {POLL_SECONDS}s")
            await asyncio.sleep(POLL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="cTrader live trading runner")
    parser.add_argument("--dry-run", action="store_true",
                        help="force dry-run (log only, no orders), overriding config.yaml "
                             "execution.dry_run_default")
    parser.add_argument("--live", action="store_true",
                        help="force live order placement, overriding config.yaml "
                             "execution.dry_run_default")
    parser.add_argument("--symbol", type=str, default=None, help="override symbol")
    parser.add_argument("--use-trained-params", action="store_true",
                        help="override config.yaml params with registry best_params (opt-in)")
    parser.add_argument("--registry-path", type=str, default=None,
                        help="override registry JSON path (defaults to config.yaml training.registry_path)")
    args = parser.parse_args()

    if args.dry_run and args.live:
        parser.error("--dry-run and --live are mutually exclusive")

    # config.yaml's execution.dry_run_default used to be dead config — this
    # process's real default was always dry_run=False (live orders placed)
    # regardless of what config.yaml said, since --dry-run is a store_true
    # flag with no way to express "false" from the CLI. It's wired up now:
    # an explicit --dry-run/--live flag always wins; with neither passed,
    # config.yaml's execution.dry_run_default decides. Ships as `false` in
    # config.yaml (see that file) so this fix does not silently change any
    # existing no-flags deployment's live-trading behavior.
    if args.live:
        dry_run = False
    elif args.dry_run:
        dry_run = True
    else:
        dry_run = bool(SETTINGS.get("execution", {}).get("dry_run_default", False))

    if dry_run:
        print("[dry-run] no orders will be placed")

    try:
        asyncio.run(run_live(
            dry_run=dry_run,
            symbol=args.symbol,
            use_trained_params=args.use_trained_params,
            registry_path=args.registry_path,
        ))
    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        remove_kill_switch()


if __name__ == "__main__":
    main()
