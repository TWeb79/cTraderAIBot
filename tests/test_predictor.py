"""Tests for the next-bar predictor (auto-mode brain)."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pandas as pd

from ctrader_bot.analysis.predictor import predict_next
from ctrader_bot.training.registry import ParameterRegistry


def _enriched_bars(last_close: float = 95.0) -> pd.DataFrame:
    base = datetime(2026, 8, 19, 13, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(10):
        ts = base + timedelta(minutes=5 * i)
        rows.append({
            "timestamp": ts, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
            "volume": 100.0, "session_date": base.date(),
            "poc_prev": 100.0, "vah_prev": 105.0, "val_prev": 95.0, "close_prev": 99.5,
            "in_gap_window": False, "gap_direction": "none", "touched_close_prev": False,
            "atr": 1.0, "regime": "RANGE",
        })
    last = rows[-1]
    last.update(close=last_close, low=last_close - 0.5, high=last_close + 0.5)
    return pd.DataFrame(rows)


def test_predict_range_fade_long():
    pred = predict_next(_enriched_bars(95.0), strategy_name="volume_profile_fade", use_trained=False)
    assert pred.direction == "LONG"
    assert pred.reason == "range_fade_val"
    assert pred.entry == 95.0
    assert pred.stop == 94.5
    assert pred.target == 100.0
    assert pred.rr is not None and pred.rr > 1
    assert pred.likelihood == 0.5  # untrained -> neutral prior
    assert pred.source == "signal"


def test_predict_strategy_disabled_setup_is_flat():
    # ny_gap_fill only enables gap_fill; a range fade is disabled -> FLAT.
    pred = predict_next(_enriched_bars(95.0), strategy_name="ny_gap_fill", use_trained=False)
    assert pred.direction == "FLAT"
    assert pred.source == "disabled"


def test_predict_trained_likelihood_weights_history(tmp_path):
    reg = ParameterRegistry(tmp_path / "reg.json")
    reg.save_best_params(
        {"level_proximity_atr_mult": 0.2, "breakout_confirm_atr_mult": 0.15,
         "trend_direction_lookback": 20, "risk_per_trade_pct": 1.5, "min_stop_atr_mult": 0.5},
        {"total_return_pct": 10.0, "max_drawdown_pct": 2.0, "win_rate": 0.8, "n_trades": 100},
        source="optimize",
    )
    reg.append_live_feedback({"setup_tag": "range_fade_val", "regime": "RANGE",
                               "r_multiple": 1.2, "pnl": 20.0, "entry_price": 95.0, "atr": 1.0})

    pred = predict_next(_enriched_bars(95.0), strategy_name="volume_profile_fade",
                        use_trained=True, registry_path=str(tmp_path / "reg.json"))
    assert pred.direction == "LONG"
    # trained likelihood should exceed the neutral 0.5 prior
    assert pred.likelihood > 0.5
    assert "trained" in pred.note


def test_predict_no_bars_is_flat():
    pred = predict_next(pd.DataFrame(), strategy_name="balanced", use_trained=False)
    assert pred.direction == "FLAT"
    assert pred.source == "no-data"
