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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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


# ── MCP client wrapper ────────────────────────────────────────────────────

from ctrader_bot.mcp_client import CTraderMCPClient


# ── Journal ───────────────────────────────────────────────────────────────

from ctrader_bot.journal.store import Journal


DB_PATH = str(_project_root() / "trade_journal.sqlite3")


# ── Strategy / Risk imports ───────────────────────────────────────────────

from ctrader_bot.backtest.engine import prepare_backtest_bars
from ctrader_bot.indicators.regime import adx_di, classify_regime
from ctrader_bot.risk.risk_manager import RiskManager, RiskLimits, compute_stop_distance, estimate_value_per_point_per_lot
from ctrader_bot.strategy.levels import (
    attach_prior_session_levels,
    compute_ny_open_gap_state,
    compute_session_levels,
)
from ctrader_bot.strategy.signals import evaluate_bar, Side, Signal
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


def _build_decision(signal: Signal) -> TradeDecision:
    rr = 0.0
    if signal.stop_price != signal.entry_price:
        rr = abs(signal.target_price - signal.entry_price) / abs(signal.stop_price - signal.entry_price)
    return TradeDecision(
        action=signal.side.value,
        confidence=0.0,
        entry_type="market",
        entry_price=signal.entry_price,
        stop_loss=signal.stop_price,
        take_profit=signal.target_price,
        risk_reward_ratio=rr,
        reasoning=signal.reason,
        invalidation_condition="stop hit or opposite signal generated",
    )


def _build_reflection(pnl: float, risk_amount: float, signal: Signal) -> TradeReflection:
    r_multiple = 0.0
    if risk_amount > 0:
        r_multiple = pnl / risk_amount
    outcome = "WIN" if r_multiple > 0 else "LOSS" if r_multiple < 0 else "BREAKEVEN"
    return TradeReflection(
        outcome=outcome,
        r_multiple=r_multiple,
        what_matched_expectation="",
        what_diverged="",
        lesson=f"Closed as {outcome} on {signal.reason}",
        setup_tag=signal.reason,
    )


def _risk_limits_from_settings(settings: dict[str, Any]) -> RiskLimits:
    r = settings["risk"]
    return RiskLimits(
        risk_per_trade_pct=r["risk_per_trade_pct"],
        max_daily_loss_pct=r["max_daily_loss_pct"],
        max_open_risk_pct=r.get("max_open_risk_pct", 10.0),
        min_stop_atr_mult=r.get("min_stop_atr_mult", 0.5),
    )


# ── Main loop ─────────────────────────────────────────────────────────────

async def run_one_cycle(mcp: CTraderMCPClient, journal: Journal,
                        risk_manager: RiskManager, symbol: str, timeframe: str,
                        profile_timeframe: str, settings: dict[str, Any],
                        dry_run: bool = False,
                        registry: ParameterRegistry | None = None) -> None:
    """Execute one deterministic decision cycle."""
    if not check_kill_switch():
        print("[kill] stop requested")
        return

    from_dt = datetime.now(timezone.utc) - timedelta(days=1)
    to_dt = datetime.now(timezone.utc)

    signal_bars_raw = await mcp.get_trendbars(symbol, timeframe, from_dt, to_dt, limit=BARS_FOR_CONTEXT)
    if not signal_bars_raw:
        print("[cycle] no signal bars returned")
        return

    profile_bars_raw = await mcp.get_trendbars(symbol, profile_timeframe, from_dt, to_dt, limit=1000)
    if not profile_bars_raw:
        print("[cycle] no profile bars returned")
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
    }
    bars = prepare_backtest_bars(signal_df, profile_df, cfg)

    if bars.empty or len(bars) == 0:
        print("[cycle] no enriched bars")
        return

    latest = bars.iloc[-1]
    lookback = settings["signals"].get("trend_direction_lookback", 20)
    recent_closes = bars["close"].iloc[max(0, len(bars) - lookback - 1):]
    atr_val = latest.get("atr")

    signal = evaluate_bar(
        latest, recent_closes, atr_val,
        level_proximity_atr_mult=settings["signals"]["level_proximity_atr_mult"],
        breakout_confirm_atr_mult=settings["signals"]["breakout_confirm_atr_mult"],
        trend_direction_lookback=lookback,
    )

    if signal is None:
        print("[cycle] no signal")
        return

    account_raw = await mcp.get_balance()
    equity = account_raw.get("equity", account_raw.get("balance", 0))

    deals = await mcp.get_deals(symbol=symbol, count=200)
    vpp = estimate_value_per_point_per_lot(deals, symbol)
    if vpp is None:
        print("[cycle] cannot estimate value_per_point_per_lot")
        return

    entry_price = signal.entry_price
    raw_stop_distance = abs(entry_price - signal.stop_price)
    stop_distance = compute_stop_distance(atr_val, raw_stop_distance, settings["risk"]["min_stop_atr_mult"])

    if signal.side == Side.BUY:
        stop_price = entry_price - stop_distance
    else:
        stop_price = entry_price + stop_distance

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
        print("[cycle] trade not sized")
        return

    risk_amount = stop_distance * volume * vpp

    side = "buy" if signal.side == Side.BUY else "sell"
    order_args: dict[str, Any] = {
        "symbolName": symbol,
        "side": side,
        "volume": volume,
        "volumeType": "lots",
    }

    pip_size = symbol_details.get("pipSize", 0.0001)
    stop_pips = abs(stop_price - entry_price) / pip_size
    order_args["stopLossPips"] = stop_pips

    if signal.target_price:
        tp_pips = abs(signal.target_price - entry_price) / pip_size
        order_args["takeProfitPips"] = tp_pips

    if dry_run:
        print(f"[dry-run] signal={signal.reason} side={side} volume={volume} stop_pips={stop_pips:.1f}")
        return

    placed = await mcp.place_market_order(**order_args)
    print(f"[order] placed: {placed}")

    position_id = placed.get("positionId") or placed.get("position_id")
    if not position_id:
        print("[order] no position id returned")
        return

    trade_id = str(position_id)
    risk_manager.register_open_trade(trade_id, risk_amount)

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

    pnl = 0.0
    for d in close_info.get("deals", []):
        if d.get("symbolName") == symbol:
            pnl += d.get("grossProfit", 0.0)

    risk_manager.register_closed_trade(trade_id, pnl)

    decision = _build_decision(signal)
    reflection = _build_reflection(pnl, risk_amount, signal)
    journal.record_trade(decision, reflection, symbol)
    print(f"[reflection] {reflection.outcome} {reflection.r_multiple:.2f}R")

    # Write-only feedback: append the live outcome to the registry so the
    # optimizer/retrain can later incorporate real-market performance. This
    # never influences the live decision (determinism preserved).
    if registry is not None:
        registry.append_live_feedback({
            "setup_tag": signal.reason,
            "regime": str(latest.get("regime", "UNKNOWN")),
            "r_multiple": reflection.r_multiple,
            "pnl": pnl,
            "entry_price": signal.entry_price,
            "atr": atr_val if atr_val is not None else 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


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

    async with CTraderMCPClient(secrets["ctrader_mcp_url"]) as mcp:
        positions = await mcp.get_positions()
        journal.save_cycle_state([str(p.get("id")) for p in positions])

        if secrets.get("demo_mode"):
            try:
                await mcp.assert_demo_account(int(secrets.get("ctrader_account_id", 0)))
            except Exception as e:
                print(f"[warn] demo account check failed: {e}")

        print(f"Running live loop for {symbol} (dry_run={dry_run}, use_trained_params={use_trained_params})")
        print("Create .kill_switch to stop gracefully.")

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


def main() -> None:
    parser = argparse.ArgumentParser(description="cTrader live trading runner")
    parser.add_argument("--dry-run", action="store_true", help="log only, no orders")
    parser.add_argument("--symbol", type=str, default=None, help="override symbol")
    parser.add_argument("--use-trained-params", action="store_true",
                        help="override config.yaml params with registry best_params (opt-in)")
    parser.add_argument("--registry-path", type=str, default=None,
                        help="override registry JSON path (defaults to config.yaml training.registry_path)")
    args = parser.parse_args()

    if args.dry_run:
        print("[dry-run] no orders will be placed")

    try:
        asyncio.run(run_live(
            dry_run=args.dry_run,
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
