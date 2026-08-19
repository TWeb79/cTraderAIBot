"""Incremental re-training around the registry's current best params.

Runs a narrow grid search (±20% for continuous params, ±5 for the discrete
``trend_direction_lookback``) over historical data, then updates the registry
only if the new composite score beats the baseline by at least
``min_improvement_pct``.

This module lives in the ``ctrader_bot.training`` package (not a top-level
script) so it can be imported by ``scripts/run_training.py`` and unit-tested
without the ``scripts/`` directory on ``sys.path``.
"""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT / "src") not in __import__("sys").path:
    __import__("sys").path.insert(0, str(PROJECT_ROOT / "src"))

from ctrader_bot.config import load_secrets, load_settings
from ctrader_bot.mcp_client import CTraderMCPClient
from ctrader_bot.risk.risk_manager import RiskLimits, estimate_value_per_point_per_lot
from ctrader_bot.training.optimizer import (
    _blend_metrics,
    _build_live_synthetic,
    _composite_score,
    _fetch_data,
    _prepare,
    _run_backtest_sync,
)
from ctrader_bot.training.registry import PARAM_KEYS, ParameterRegistry

CONTINUOUS_KEYS = {
    "level_proximity_atr_mult",
    "breakout_confirm_atr_mult",
    "risk_per_trade_pct",
    "min_stop_atr_mult",
}
DISCRETE_STEP = {"trend_direction_lookback": 5}


def _round_param(key: str, value: float) -> float:
    if key == "trend_direction_lookback":
        return int(round(value))
    return round(value, 4)


def _build_narrow_grid(best: dict[str, Any]) -> dict[str, list[Any]]:
    """Build a narrow grid around the current best params.

    Continuous params vary by ±20%; the discrete ``trend_direction_lookback``
    varies by ±5. Always includes the base value so the baseline is reachable.
    """
    grid: dict[str, list[Any]] = {}
    for key in PARAM_KEYS:
        if key not in best or best[key] is None:
            continue
        base = float(best[key])
        if key in CONTINUOUS_KEYS:
            values = [base * 0.8, base, base * 1.2]
        else:
            step = DISCRETE_STEP.get(key, 2)
            values = [base - step, base, base + step]
        rounded = []
        for v in values:
            rv = _round_param(key, v)
            if rv > 0 and rv not in rounded:
                rounded.append(rv)
        grid[key] = rounded
    return grid


def _score_params(params: dict[str, Any], bars, value_per_point_per_lot: float,
                  symbol_meta: dict, settings: dict, risk_base: dict,
                  live_synth: dict | None) -> tuple[float, dict[str, Any]]:
    risk_limits = RiskLimits(
        risk_per_trade_pct=params["risk_per_trade_pct"],
        max_daily_loss_pct=risk_base["max_daily_loss_pct"],
        max_open_risk_pct=risk_base.get("max_open_risk_pct", 10.0),
        min_stop_atr_mult=params["min_stop_atr_mult"],
    )
    metrics = _run_backtest_sync(
        bars, risk_limits, settings["backtest"]["initial_equity"],
        value_per_point_per_lot, symbol_meta, params, settings,
    )
    if live_synth and live_synth["n"] > 0:
        blended = _blend_metrics(metrics, live_synth)
        score = _composite_score(blended)
    else:
        score = _composite_score(metrics)
    return score, metrics


async def retrain(days: int = 30, symbol: str | None = None, include_live: bool = False,
                 min_improvement_pct: float = 5.0, registry_path: str | None = None,
                 silent: bool = False) -> dict[str, Any]:
    """Run incremental re-training and update the registry if improved."""
    settings = load_settings()
    secrets = load_secrets()
    symbol = symbol or settings["symbol"]
    signal_tf = settings["timeframes"]["signal"]
    profile_tf = settings["timeframes"]["profile"]

    registry = ParameterRegistry(registry_path) if registry_path else ParameterRegistry()
    best = registry.load_best_params()
    if not best:
        raise RuntimeError("No best params in registry — run `optimize` first.")

    live_synth = None
    if include_live:
        feedback_weight = float(settings.get("training", {}).get("feedback_weight", 2.0))
        live_synth = _build_live_synthetic(registry.get_live_feedback_summary(), feedback_weight)

    async with CTraderMCPClient(secrets.ctrader_mcp_url) as client:
        signal_bars, profile_bars, symbol_details, deals = await _fetch_data(
            symbol, signal_tf, profile_tf, days, client
        )
    if not signal_bars or not profile_bars:
        raise RuntimeError("No data returned from MCP — check server / date range.")

    bars = _prepare(signal_bars, profile_bars, symbol_details, settings)
    vpp = estimate_value_per_point_per_lot(deals, symbol) or 1.0
    symbol_meta = {
        "minVolume": symbol_details["minVolume"],
        "maxVolume": symbol_details["maxVolume"],
        "volumeStep": symbol_details["volumeStep"],
    }
    risk_base = settings["risk"]

    baseline_score, _ = _score_params(best, bars, vpp, symbol_meta, settings, risk_base, live_synth)
    if not silent:
        print(f"Baseline score (current best params): {baseline_score:.4f}")

    grid = _build_narrow_grid(best)
    keys = list(grid.keys())
    combinations = list(itertools.product(*[grid[k] for k in keys]))

    best_combo: dict[str, Any] | None = None
    best_score = baseline_score
    best_metrics: dict[str, Any] = {}
    for combo in combinations:
        params = dict(zip(keys, combo))
        for k in PARAM_KEYS:
            params.setdefault(k, best.get(k))
        score, metrics = _score_params(params, bars, vpp, symbol_meta, settings, risk_base, live_synth)
        if score > best_score:
            best_score = score
            best_combo = params
            best_metrics = metrics

    threshold = baseline_score * (1 + min_improvement_pct / 100.0)
    result = {
        "baseline_score": round(baseline_score, 6),
        "best_score": round(best_score, 6),
        "threshold": round(threshold, 6),
        "improved": best_combo is not None and best_score > threshold,
        "old_params": dict(best),
        "new_params": dict(best_combo) if best_combo else dict(best),
    }

    if result["improved"] and best_combo is not None:
        registry.save_best_params(best_combo, best_metrics, source="retrain")
        if not silent:
            print(f"Improved! {baseline_score:.4f} -> {best_score:.4f}")
            for k in PARAM_KEYS:
                if best.get(k) != best_combo.get(k):
                    print(f"  {k}: {best.get(k)} -> {best_combo.get(k)}")
    elif not silent:
        print(f"No improvement above threshold {threshold:.4f}; registry unchanged.")

    return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Incremental re-training around best params")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--include-live", action="store_true", help="Blend registry live feedback into scoring")
    parser.add_argument("--min-improvement", type=float, default=5.0, help="Min percent improvement to accept new params")
    parser.add_argument("--registry-path", type=str, default=None)
    args = parser.parse_args()

    asyncio.run(retrain(
        days=args.days, symbol=args.symbol, include_live=args.include_live,
        min_improvement_pct=args.min_improvement, registry_path=args.registry_path,
    ))


if __name__ == "__main__":
    main()
