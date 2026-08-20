"""Regression tests for training/optimizer.py's `_run_backtest_sync()` return
shape — the source of a real registry-corruption bug found while auditing
why "not sure if training works" (implementationplan.md §18): every
dashboard-triggered "optimize" job was silently saving `best_params: {...all
null...}` to data/reports/parameter_registry.json, so `--use-trained-params`
and the dashboard's "use trained" auto-mode toggle never actually applied
anything (falling back to config.yaml with no visible error).

Root cause: `_run_backtest_sync` used to return `{"params": params, ...}` —
a single "params" column holding a dict object once `pd.DataFrame(results)`
built the optimizer's results table in `optimize()`. `dashboard_api.py`'s
`_run_training_job` then did `row.get("level_proximity_atr_mult")` etc.
against that flat row, which only ever hit the real *column* names — never
inside the nested dict — so every extracted value was None. No test existed
for this module before (confirmed empty tests/test_optimizer.py at audit
time), which is exactly how a DataFrame-shape bug like this survives.
"""

import numpy as np
import pandas as pd
import pytest

from ctrader_bot.backtest.engine import prepare_backtest_bars
from ctrader_bot.risk.risk_manager import RiskLimits
from ctrader_bot.training.optimizer import _composite_score, _default_grid, _run_backtest_sync


def _limits(**overrides):
    base = dict(risk_per_trade_pct=1.5, max_daily_loss_pct=5.0, max_open_risk_pct=10.0, min_stop_atr_mult=0.5)
    base.update(overrides)
    return RiskLimits(**base)


def _symbol_meta():
    return {"minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}


def _settings():
    return {
        "backtest": {"spread_points": 0.4, "commission_per_lot": 0.0, "initial_equity": 10000},
    }


def _build_synthetic_bars(n_sessions: int = 6, bars_per_session: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    price = 100.0
    for s in range(n_sessions):
        session_start = pd.Timestamp("2026-08-01T22:00:00Z") + pd.Timedelta(days=s)
        for b in range(bars_per_session):
            drift = 0.02 if s % 2 == 0 else -0.02
            price += rng.normal(drift, 0.4)
            high = price + abs(rng.normal(0, 0.3))
            low = price - abs(rng.normal(0, 0.3))
            vol = abs(rng.normal(200, 50))
            rows.append({
                "timestamp": session_start + pd.Timedelta(minutes=b),
                "open": price, "high": high, "low": low, "close": price, "volume": vol,
            })
    return pd.DataFrame(rows)


def _prepared_bars() -> pd.DataFrame:
    m1_bars = _build_synthetic_bars(n_sessions=6, bars_per_session=200)
    m5_bars = m1_bars.iloc[::5].reset_index(drop=True)
    cfg = {
        "session_rollover_utc_hour": 21,
        "pip_size": 1.0,
        "volume_profile": {"price_bin_ticks": 1, "value_area_pct": 0.70},
        "session": {"ny_open_utc": "13:30", "gap_fill_window_minutes": 60},
        "regime": {
            "adx_period": 14, "adx_trend_threshold": 25, "adx_range_threshold": 20,
            "di_separation_min": 5, "trend_confirm_bars": 6,
            "atr_expansion_factor": 1.3, "atr_median_lookback": 20,
        },
    }
    return prepare_backtest_bars(m5_bars, m1_bars, cfg)


def test_run_backtest_sync_flattens_params_into_top_level_keys():
    """The bug: params used to live only under a nested 'params' dict key,
    so a DataFrame built from these dicts had no
    level_proximity_atr_mult/etc. columns at all."""
    bars = _prepared_bars()
    params = {
        "level_proximity_atr_mult": 0.25,
        "breakout_confirm_atr_mult": 0.15,
        "trend_direction_lookback": 20,
        "risk_per_trade_pct": 1.5,
        "min_stop_atr_mult": 0.5,
    }
    metrics = _run_backtest_sync(
        bars, _limits(), 10000, 0.87, _symbol_meta(), params, _settings(),
    )

    for key, value in params.items():
        assert metrics[key] == value
    assert "params" not in metrics  # nested blob removed, not just duplicated
    assert "n_trades" in metrics and "total_return_pct" in metrics


def test_optimize_results_dataframe_has_real_param_columns():
    """End-to-end shape check for what optimize() actually builds:
    pd.DataFrame(results) must expose each grid key as its own column (what
    dashboard_api.py's `row.get("level_proximity_atr_mult")`-style
    extraction requires), not a single dict-valued 'params' column."""
    bars = _prepared_bars()
    grid = _default_grid()
    keys = list(grid.keys())
    # A handful of combinations is enough to prove the DataFrame shape;
    # the full grid is exercised by optimize() itself, not by this test.
    combos = [dict(zip(keys, values)) for values in zip(*[grid[k][:2] for k in keys])]

    results = []
    for params in combos:
        risk_limits = RiskLimits(
            risk_per_trade_pct=params["risk_per_trade_pct"],
            max_daily_loss_pct=5.0, max_open_risk_pct=10.0,
            min_stop_atr_mult=params["min_stop_atr_mult"],
        )
        metrics = _run_backtest_sync(bars, risk_limits, 10000, 0.87, _symbol_meta(), params, _settings())
        metrics["composite_score"] = _composite_score(metrics)
        results.append(metrics)

    df = pd.DataFrame(results)
    for key in keys:
        assert key in df.columns, f"missing flat column for {key} — params were nested again"
        assert df[key].notna().all()


def test_run_backtest_sync_preserves_metrics_fields():
    bars = _prepared_bars()
    params = {
        "level_proximity_atr_mult": 0.25, "breakout_confirm_atr_mult": 0.15,
        "trend_direction_lookback": 20, "risk_per_trade_pct": 1.5, "min_stop_atr_mult": 0.5,
    }
    metrics = _run_backtest_sync(bars, _limits(), 10000, 0.87, _symbol_meta(), params, _settings())
    for key in ("n_trades", "win_rate", "avg_r", "total_return_pct", "max_drawdown_pct"):
        assert key in metrics
