"""Backtest runner: fetch historical data via cTrader MCP and run the
deterministic backtest engine.

Replaces ``scripts/run_backtest.py`` with a cleaner import structure.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pandas as pd

from ctrader_bot.backtest.engine import prepare_backtest_bars, run_backtest
from ctrader_bot.backtest.report import build_report, format_report
from ctrader_bot.config import load_secrets, load_settings
from ctrader_bot.mcp_client import CTraderMCPClient
from ctrader_bot.risk.risk_manager import RiskLimits, estimate_value_per_point_per_lot


def bars_to_df(bars) -> pd.DataFrame:
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


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtest over historical data")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    parser.add_argument("--symbol", type=str, default=None, help="Override symbol")
    args = parser.parse_args()

    settings = load_settings()
    secrets = load_secrets()
    symbol = args.symbol or settings["symbol"]

    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=args.days)

    async with CTraderMCPClient(secrets.ctrader_mcp_url) as client:
        symbol_details = await client.get_symbol_details(symbol)
        print(f"Symbol: {symbol_details}")

        print(f"Fetching {settings['timeframes']['signal']} bars from {from_dt} to {to_dt} ...")
        signal_bars = await client.get_trendbars_range(
            symbol, settings["timeframes"]["signal"], from_dt, to_dt
        )
        print(f"Fetching {settings['timeframes']['profile']} bars (for volume profile) ...")
        profile_bars = await client.get_trendbars_range(
            symbol, settings["timeframes"]["profile"], from_dt, to_dt
        )

        deals = await client.get_deals(symbol=symbol, count=200)
        value_per_point_per_lot = estimate_value_per_point_per_lot(deals, symbol)
        if value_per_point_per_lot is None:
            print("WARNING: no closed deals found for this symbol to calibrate "
                  "value_per_point_per_lot; defaulting to 1.0")
            value_per_point_per_lot = 1.0
        else:
            print(f"Calibrated value_per_point_per_lot: {value_per_point_per_lot:.4f}")

    signal_df = bars_to_df(signal_bars)
    profile_df = bars_to_df(profile_bars)
    print(f"{len(signal_df)} signal bars, {len(profile_df)} profile bars fetched.")

    if signal_df.empty or profile_df.empty:
        print("No data returned — check the MCP server / date range.")
        return

    cfg = {
        "session_rollover_utc_hour": 21,
        "pip_size": symbol_details["pipSize"],
        "volume_profile": settings["volume_profile"],
        "session": settings["session"],
        "regime": settings["regime"],
    }
    bars = prepare_backtest_bars(signal_df, profile_df, cfg)

    risk_limits = RiskLimits(
        risk_per_trade_pct=settings["risk"]["risk_per_trade_pct"],
        max_daily_loss_pct=settings["risk"]["max_daily_loss_pct"],
        max_open_risk_pct=settings["risk"].get("max_open_risk_pct", 10.0),
        min_stop_atr_mult=settings["risk"]["min_stop_atr_mult"],
    )

    result = run_backtest(
        bars,
        risk_limits=risk_limits,
        initial_equity=settings["backtest"]["initial_equity"],
        value_per_point_per_lot=value_per_point_per_lot,
        symbol_meta={
            "minVolume": symbol_details["minVolume"],
            "maxVolume": symbol_details["maxVolume"],
            "volumeStep": symbol_details["volumeStep"],
        },
        spread_points=settings["backtest"]["spread_points"],
        commission_per_lot=settings["backtest"]["commission_per_lot"],
        level_proximity_atr_mult=settings["signals"]["level_proximity_atr_mult"],
        breakout_confirm_atr_mult=settings["signals"]["breakout_confirm_atr_mult"],
    )

    report = build_report(result, initial_equity=settings["backtest"]["initial_equity"])
    print(format_report(report))

    out_dir = PROJECT_ROOT / "data" / "cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    trades_df = pd.DataFrame([vars(t) for t in result.trades])
    if not trades_df.empty:
        trades_df.to_csv(out_dir / "last_backtest_trades.csv", index=False)
        print(f"Trade log written to {out_dir / 'last_backtest_trades.csv'}")
    result.equity_curve.to_csv(out_dir / "last_backtest_equity_curve.csv", index=False)


if __name__ == "__main__":
    asyncio.run(main())
