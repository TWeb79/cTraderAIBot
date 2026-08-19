"""Bar-by-bar simulated trading engine with failure analysis.

Reuses ``backtest.engine.prepare_backtest_bars`` and ``strategy.signals.evaluate_bar``
so strategy logic is never duplicated. Simulates trades in memory (no DB writes,
no MCP calls) and produces:
- A trade-level CSV
- A failure analysis markdown report

This module is **never** imported by the live runner.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ctrader_bot.backtest.engine import prepare_backtest_bars
from ctrader_bot.config import load_secrets, load_settings
from ctrader_bot.mcp_client import CTraderMCPClient
from ctrader_bot.risk.risk_manager import RiskLimits, estimate_value_per_point_per_lot
from ctrader_bot.strategy.signals import evaluate_bar, Side


def _bars_to_df(bars) -> pd.DataFrame:
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


async def _fetch_data(symbol: str, signal_tf: str, profile_tf: str, days: int, mcp: CTraderMCPClient):
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=days)

    signal_bars = await mcp.get_trendbars_range(symbol, signal_tf, from_dt, to_dt)
    profile_bars = await mcp.get_trendbars_range(symbol, profile_tf, from_dt, to_dt)
    symbol_details = await mcp.get_symbol_details(symbol)
    deals = await mcp.get_deals(symbol=symbol, count=200)

    return signal_bars, profile_bars, symbol_details, deals


def _prepare(signal_bars, profile_bars, symbol_details: dict, settings: dict) -> pd.DataFrame:
    signal_df = _bars_to_df(signal_bars)
    profile_df = _bars_to_df(profile_bars)
    cfg = {
        "session_rollover_utc_hour": 21,
        "pip_size": symbol_details.get("pipSize", 1.0),
        "volume_profile": settings["volume_profile"],
        "session": settings["session"],
        "regime": settings["regime"],
    }
    return prepare_backtest_bars(signal_df, profile_df, cfg)


@dataclass
class SimTrade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp | None
    symbol: str
    side: str
    setup_tag: str
    regime: str
    entry_price: float
    stop_price: float
    target_price: float
    volume: float
    atr: float
    entry_poc: float | None = None
    entry_vah: float | None = None
    entry_val: float | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl: float | None = None
    bars_held: int = 0
    entry_data: dict[str, Any] = field(default_factory=dict)


def _simulate(bars: pd.DataFrame, settings: dict, symbol_meta: dict,
              value_per_point_per_lot: float) -> tuple[list[SimTrade], list[dict[str, Any]]]:
    """Step through bars bar-by-bar, simulating trades.

    Returns (trades, entry_snapshots).
    """
    lookback = 20
    open_trade: SimTrade | None = None
    trades: list[SimTrade] = []
    entry_snapshots: list[dict[str, Any]] = []

    for i in range(len(bars)):
        row = bars.iloc[i]

        if open_trade is not None:
            open_trade.bars_held += 1
            side_mult = 1 if open_trade.side == "BUY" else -1

            hit_stop = (row["low"] <= open_trade.stop_price) if open_trade.side == "BUY" else (row["high"] >= open_trade.stop_price)
            hit_target = (row["high"] >= open_trade.target_price) if open_trade.side == "BUY" else (row["low"] <= open_trade.target_price)

            if hit_stop and hit_target:
                if open_trade.side == "BUY":
                    hit_stop_first = (open_trade.stop_price - open_trade.entry_price) <= (open_trade.target_price - open_trade.entry_price)
                else:
                    hit_stop_first = (open_trade.entry_price - open_trade.stop_price) <= (open_trade.entry_price - open_trade.target_price)
                if hit_stop_first:
                    exit_price = open_trade.stop_price
                    exit_reason = "stop"
                else:
                    exit_price = open_trade.target_price
                    exit_reason = "target"
            elif hit_stop:
                exit_price = open_trade.stop_price
                exit_reason = "stop"
            elif hit_target:
                exit_price = open_trade.target_price
                exit_reason = "target"
            else:
                exit_price = None
                exit_reason = None

            if exit_price is not None:
                direction = 1 if open_trade.side == "BUY" else -1
                gross = (exit_price - open_trade.entry_price) * direction * open_trade.volume * value_per_point_per_lot
                open_trade.exit_price = exit_price
                open_trade.exit_reason = exit_reason
                open_trade.exit_time = row["timestamp"]
                open_trade.pnl = gross
                trades.append(open_trade)
                open_trade = None

        if open_trade is None:
            recent_closes = bars["close"].iloc[max(0, i - lookback - 1): i + 1]
            atr_val = row.get("atr")
            signal = evaluate_bar(
                row, recent_closes, atr_val,
                level_proximity_atr_mult=settings["signals"]["level_proximity_atr_mult"],
                breakout_confirm_atr_mult=settings["signals"]["breakout_confirm_atr_mult"],
                trend_direction_lookback=lookback,
            )

            if signal is not None:
                volume = 1.0  # fixed unit size for simulation
                risk_amount = abs(signal.entry_price - signal.stop_price) * volume * value_per_point_per_lot

                open_trade = SimTrade(
                    entry_time=row["timestamp"],
                    exit_time=None,
                    symbol=settings.get("symbol", "US500"),
                    side=signal.side.value,
                    setup_tag=signal.reason,
                    regime=str(row.get("regime", "UNKNOWN")),
                    entry_price=signal.entry_price,
                    stop_price=signal.stop_price,
                    target_price=signal.target_price,
                    volume=volume,
                    atr=atr_val if atr_val is not None else 0.0,
                    entry_poc=row.get("poc_prev"),
                    entry_vah=row.get("vah_prev"),
                    entry_val=row.get("val_prev"),
                    entry_data={
                        "adx": row.get("adx"),
                        "plus_di": row.get("plus_di"),
                        "minus_di": row.get("minus_di"),
                        "close_prev": row.get("close_prev"),
                        "in_gap_window": row.get("in_gap_window"),
                        "gap_direction": row.get("gap_direction"),
                    },
                )
                entry_snapshots.append({
                    "timestamp": row["timestamp"],
                    "setup_tag": signal.reason,
                    "regime": str(row.get("regime")),
                    "entry_price": signal.entry_price,
                    "atr": atr_val,
                    "poc_prev": row.get("poc_prev"),
                    "vah_prev": row.get("vah_prev"),
                    "val_prev": row.get("val_prev"),
                    "adx": row.get("adx"),
                    "plus_di": row.get("plus_di"),
                    "minus_di": row.get("minus_di"),
                    "close_prev": row.get("close_prev"),
                    "in_gap_window": row.get("in_gap_window"),
                    "gap_direction": row.get("gap_direction"),
                })

    return trades, entry_snapshots


def _trade_to_dict(t: SimTrade) -> dict[str, Any]:
    return {
        "entry_time": t.entry_time,
        "exit_time": t.exit_time,
        "symbol": t.symbol,
        "side": t.side,
        "setup_tag": t.setup_tag,
        "regime": t.regime,
        "entry_price": t.entry_price,
        "stop_price": t.stop_price,
        "target_price": t.target_price,
        "exit_price": t.exit_price,
        "exit_reason": t.exit_reason,
        "pnl": t.pnl,
        "r_multiple": (t.pnl / (abs(t.entry_price - t.stop_price) * t.volume)) if abs(t.entry_price - t.stop_price) > 0 and t.pnl is not None else 0.0,
        "bars_held": t.bars_held,
        "atr": t.atr,
        "entry_poc": t.entry_poc,
        "entry_vah": t.entry_vah,
        "entry_val": t.entry_val,
    }


def _failure_analysis(trades: list[SimTrade]) -> str:
    losses = [t for t in trades if t.pnl is not None and t.pnl < 0]
    if not losses:
        return "No losing trades in this simulation."

    by_setup: dict[str, list[SimTrade]] = {}
    by_regime: dict[str, list[SimTrade]] = {}
    for t in losses:
        by_setup.setdefault(t.setup_tag, []).append(t)
        by_regime.setdefault(t.regime, []).append(t)

    lines = [
        "# Failure Analysis",
        f"Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
        f"Total simulated trades: {len(trades)}",
        f"Losing trades: {len(losses)}",
        "",
        "## By Setup Tag",
    ]
    for tag, tlist in sorted(by_setup.items(), key=lambda x: -len(x[1])):
        avg_r = sum(t.pnl for t in tlist) / len(tlist) if tlist else 0
        lines.append(f"- **{tag}**: {len(tlist)} losses, avg PnL={avg_r:.2f}")

    lines.append("")
    lines.append("## By Regime")
    for regime, tlist in sorted(by_regime.items(), key=lambda x: -len(x[1])):
        avg_r = sum(t.pnl for t in tlist) / len(tlist) if tlist else 0
        lines.append(f"- **{regime}**: {len(tlist)} losses, avg PnL={avg_r:.2f}")

    lines.append("")
    lines.append("## Entry Data Snapshots (losers)")
    lines.append("| timestamp | setup_tag | regime | entry_price | atr | adx |")
    lines.append("|-----------|-----------|--------|-------------|-----|-----|")
    for t in losses[:20]:
        ed = t.entry_data or {}
        lines.append(
            f"| {t.entry_time:%Y-%m-%d %H:%M} | {t.setup_tag} | {t.regime} | {t.entry_price:.2f} | {t.atr:.4f} | {ed.get('adx', '—')} |"
        )

    return "\n".join(lines)


async def simulate(days: int = 30, symbol: str | None = None,
                   analyze_failures: bool = True) -> tuple[pd.DataFrame, str | None]:
    settings = load_settings()
    secrets = load_secrets()
    symbol = symbol or settings["symbol"]
    signal_tf = settings["timeframes"]["signal"]
    profile_tf = settings["timeframes"]["profile"]

    async with CTraderMCPClient(secrets["ctrader_mcp_url"]) as client:
        signal_bars, profile_bars, symbol_details, deals = await _fetch_data(
            symbol, signal_tf, profile_tf, days, client
        )

    if not signal_bars or not profile_bars:
        raise RuntimeError("No data returned from MCP.")

    bars = _prepare(signal_bars, profile_bars, symbol_details, settings)
    value_per_point_per_lot = estimate_value_per_point_per_lot(deals, symbol)
    if value_per_point_per_lot is None:
        value_per_point_per_lot = 1.0

    trades, snapshots = _simulate(bars, settings, {
        "minVolume": symbol_details["minVolume"],
        "maxVolume": symbol_details["maxVolume"],
        "volumeStep": symbol_details["volumeStep"],
    }, value_per_point_per_lot)

    trade_rows = [_trade_to_dict(t) for t in trades]
    df = pd.DataFrame(trade_rows)

    failure_report = None
    if analyze_failures and not df.empty:
        failure_report = _failure_analysis(trades)

    return df, failure_report


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Simulated trading engine with failure analysis")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    parser.add_argument("--symbol", type=str, default=None, help="Override symbol")
    parser.add_argument("--analyze-failures", action="store_true", help="Generate failure analysis report")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path")
    parser.add_argument("--report", type=str, default=None, help="Failure analysis markdown path")
    args = parser.parse_args()

    df, report = asyncio.run(simulate(days=args.days, symbol=args.symbol, analyze_failures=args.analyze_failures))

    if df.empty:
        print("No simulated trades generated.")
        return

    if args.output:
        out = Path(args.output)
    else:
        out = PROJECT_ROOT / "data" / "reports" / f"simulated_trades_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"Trades written to {out}")
    print(df.describe().to_string())

    if report:
        if args.report:
            rpath = Path(args.report)
        else:
            rpath = PROJECT_ROOT / "data" / "reports" / f"failure_analysis_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.md"
        rpath.parent.mkdir(parents=True, exist_ok=True)
        rpath.write_text(report)
        print(f"Failure analysis written to {rpath}")


if __name__ == "__main__":
    main()
