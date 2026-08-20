"""Parameter grid search optimizer using the existing backtest engine.

Reuses ``backtest.engine.prepare_backtest_bars`` and ``backtest.engine.run_backtest``
so strategy logic is never duplicated. Outputs a CSV report ranked by a composite
score.

This module is **never** imported by the live runner. It is intended to be run
offline to inform manual parameter tuning.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ctrader_bot.backtest.engine import prepare_backtest_bars, run_backtest
from ctrader_bot.config import load_secrets, load_settings
from ctrader_bot.mcp_client import CTraderMCPClient
from ctrader_bot.risk.risk_manager import RiskLimits, estimate_value_per_point_per_lot
from ctrader_bot.training.registry import ParameterRegistry


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


def _default_grid() -> dict[str, list[Any]]:
    return {
        "level_proximity_atr_mult": [0.15, 0.25, 0.35],
        "breakout_confirm_atr_mult": [0.10, 0.15, 0.20],
        "trend_direction_lookback": [10, 20, 30],
        "risk_per_trade_pct": [0.5, 1.0, 1.5, 2.0],
        "min_stop_atr_mult": [0.3, 0.5, 0.75],
    }


def _composite_score(result_metrics: dict[str, Any]) -> float:
    total_return = result_metrics.get("total_return_pct", 0.0)
    max_dd = result_metrics.get("max_drawdown_pct", 100.0)
    win_rate = result_metrics.get("win_rate", 0.0)
    n_trades = result_metrics.get("n_trades", 0)

    if n_trades == 0 or max_dd <= 0:
        return 0.0

    return (total_return / max_dd) * (win_rate ** 0.5) * (n_trades ** 0.25)


def _build_live_synthetic(live_summary: dict[str, Any], feedback_weight: float) -> dict[str, Any]:
    """Turn registry live feedback into synthetic trade aggregates.

    Each setup-tag bucket is expanded into ``feedback_weight`` synthetic
    trades (rounded), each carrying the bucket's average R. This lets live
    performance bias the composite score without re-running the backtest.
    """
    by_tag = live_summary.get("by_setup_tag", {})
    n = 0
    wins = 0
    r_sum = 0.0
    synth_return = 0.0
    k = max(1, int(round(feedback_weight)))
    for bucket in by_tag.values():
        avg_r = float(bucket.get("avg_r", 0.0))
        for _ in range(k):
            n += 1
            if avg_r > 0:
                wins += 1
            r_sum += avg_r
            synth_return += avg_r  # approximate 1R ≈ 1% for scoring blend
    return {"n": n, "wins": wins, "r_sum": r_sum, "synth_return": synth_return}


def _blend_metrics(backtest: dict[str, Any], live_synth: dict[str, Any]) -> dict[str, Any]:
    """Merge backtest metrics with synthetic live-trade metrics."""
    nt = backtest.get("n_trades", 0)
    if nt == 0 and live_synth["n"] == 0:
        return dict(backtest)
    bt_wins = round(backtest.get("win_rate", 0.0) * nt) if nt else 0
    bt_r = backtest.get("avg_r", 0.0) * nt
    bt_ret = backtest.get("total_return_pct", 0.0) * nt
    tot_n = nt + live_synth["n"]
    if tot_n == 0:
        return dict(backtest)
    return {
        "n_trades": tot_n,
        "win_rate": (bt_wins + live_synth["wins"]) / tot_n,
        "avg_r": (bt_r + live_synth["r_sum"]) / tot_n,
        "total_return_pct": (bt_ret + live_synth["synth_return"]) / tot_n,
        "max_drawdown_pct": backtest.get("max_drawdown_pct", 100.0),
    }


def _run_backtest_sync(bars: pd.DataFrame, risk_limits: RiskLimits, initial_equity: float,
                       value_per_point_per_lot: float, symbol_meta: dict, params: dict[str, Any],
                       settings: dict) -> dict[str, Any]:
    result = run_backtest(
        bars,
        risk_limits=risk_limits,
        initial_equity=initial_equity,
        value_per_point_per_lot=value_per_point_per_lot,
        symbol_meta=symbol_meta,
        spread_points=settings["backtest"]["spread_points"],
        commission_per_lot=settings["backtest"]["commission_per_lot"],
        level_proximity_atr_mult=params["level_proximity_atr_mult"],
        breakout_confirm_atr_mult=params["breakout_confirm_atr_mult"],
        trend_direction_lookback=params["trend_direction_lookback"],
    )

    trades = result.trades
    equity_curve = result.equity_curve

    pnl_values = [t.pnl for t in trades if t.pnl is not None]
    n_trades = len(trades)
    wins = [p for p in pnl_values if p > 0]
    losses = [p for p in pnl_values if p <= 0]

    total_return_pct = 0.0
    max_drawdown_pct = 0.0
    win_rate = 0.0
    avg_r = 0.0

    if not equity_curve.empty and len(equity_curve) > 1:
        eq = equity_curve["equity"].values
        start = eq[0]
        end = eq[-1]
        total_return_pct = ((end - start) / start) * 100 if start != 0 else 0.0

        peak = eq[0]
        max_dd = 0.0
        for v in eq:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak != 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        max_drawdown_pct = max_dd * 100

    if n_trades > 0:
        win_rate = len(wins) / n_trades
        avg_r = sum(pnl_values) / n_trades

    return {
        "n_trades": n_trades,
        "win_rate": round(win_rate, 4),
        "avg_r": round(avg_r, 4),
        "total_return_pct": round(total_return_pct, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        # Flattened directly into this dict (not nested under a "params"
        # key) so pd.DataFrame(results) in optimize() gets real
        # level_proximity_atr_mult/breakout_confirm_atr_mult/etc. columns.
        # A nested dict here used to produce a single "params" column
        # holding dict objects — dashboard_api.py's `row.get(k)` extraction
        # (and this project's ParameterRegistry) then always got None for
        # every param, since no top-level column had those names. That's
        # why the registry's best_params ended up all-null despite real
        # backtest results: optimize() ran fine, but every dashboard-
        # triggered "optimize" job silently saved null params, so
        # --use-trained-params / auto-mode's "use_trained" toggle never
        # actually overrode anything (live_runner.py's
        # _apply_trained_params()'s `is not None` guard just skipped every
        # key and fell back to config.yaml).
        **params,
    }


async def optimize(days: int = 30, symbol: str | None = None, top_n: int = 10,
                    output_path: str | None = None, include_live: bool = False,
                    registry_path: str | None = None) -> pd.DataFrame:
    settings = load_settings()
    secrets = load_secrets()
    symbol = symbol or settings["symbol"]
    signal_tf = settings["timeframes"]["signal"]
    profile_tf = settings["timeframes"]["profile"]

    live_synth = {"n": 0, "wins": 0, "r_sum": 0.0, "synth_return": 0.0}
    if include_live:
        training = settings.get("training", {})
        feedback_weight = float(training.get("feedback_weight", 2.0))
        registry = ParameterRegistry(registry_path) if registry_path else ParameterRegistry()
        live_synth = _build_live_synthetic(registry.get_live_feedback_summary(), feedback_weight)

    async with CTraderMCPClient(secrets.ctrader_mcp_url) as client:
        signal_bars, profile_bars, symbol_details, deals = await _fetch_data(
            symbol, signal_tf, profile_tf, days, client
        )

    if not signal_bars or not profile_bars:
        raise RuntimeError("No data returned from MCP — check server / date range.")

    bars = _prepare(signal_bars, profile_bars, symbol_details, settings)
    value_per_point_per_lot = estimate_value_per_point_per_lot(deals, symbol)
    if value_per_point_per_lot is None:
        value_per_point_per_lot = 1.0

    grid = _default_grid()
    keys = list(grid.keys())
    combinations = list(itertools.product(*[grid[k] for k in keys]))

    base_limits = settings["risk"]
    symbol_meta = {
        "minVolume": symbol_details["minVolume"],
        "maxVolume": symbol_details["maxVolume"],
        "volumeStep": symbol_details["volumeStep"],
    }
    initial_equity = settings["backtest"]["initial_equity"]

    results = []
    for combo in combinations:
        params = dict(zip(keys, combo))
        risk_limits = RiskLimits(
            risk_per_trade_pct=params["risk_per_trade_pct"],
            max_daily_loss_pct=base_limits["max_daily_loss_pct"],
            max_open_risk_pct=base_limits.get("max_open_risk_pct", 10.0),
            min_stop_atr_mult=params["min_stop_atr_mult"],
        )
        metrics = _run_backtest_sync(
            bars, risk_limits, initial_equity,
            value_per_point_per_lot, symbol_meta, params, settings
        )
        backtest_score = _composite_score(metrics)
        metrics["backtest_score"] = round(backtest_score, 6)
        if include_live and live_synth["n"] > 0:
            blended = _blend_metrics(metrics, live_synth)
            metrics["composite_score"] = round(_composite_score(blended), 6)
        else:
            metrics["composite_score"] = round(backtest_score, 6)
        results.append(metrics)

    df = pd.DataFrame(results)
    df = df.sort_values("composite_score", ascending=False).reset_index(drop=True)
    top = df.head(top_n)

    if output_path:
        out = Path(output_path)
    else:
        out = PROJECT_ROOT / "data" / "reports" / f"param_optimization_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    top.to_csv(out, index=False)

    return top


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Parameter optimizer for cTrader strategy")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days")
    parser.add_argument("--symbol", type=str, default=None, help="Override symbol")
    parser.add_argument("--top", type=int, default=10, help="Number of top results to keep")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path")
    parser.add_argument("--include-live", action="store_true",
                        help="Blend registry live feedback into scoring")
    parser.add_argument("--registry-path", type=str, default=None, help="Override registry JSON path")
    args = parser.parse_args()

    top = asyncio.run(optimize(
        days=args.days, symbol=args.symbol, top_n=args.top,
        output_path=args.output, include_live=args.include_live,
        registry_path=args.registry_path,
    ))
    print(top.to_string(index=False))


if __name__ == "__main__":
    main()
