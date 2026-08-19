import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from ctrader_bot.training.optimizer import (
    _blend_metrics,
    _build_live_synthetic,
    _composite_score,
    _default_grid,
    _prepare,
    _run_backtest_sync,
)
from ctrader_bot.training.simulator import _failure_analysis, _simulate


def _make_bar(ts, close, volume=100.0):
    class Bar:
        def __init__(self, timestamp, open_, high, low, close, volume):
            self.timestamp = timestamp
            self.open = open_
            self.high = high
            self.low = low
            self.close = close
            self.volume = volume
    return Bar(ts, close, close + 0.5, close - 0.5, close, volume)


def _make_bars(n=60, start_price=100.0):
    bars = []
    now = datetime.now(timezone.utc)
    for i in range(n):
        ts = now - timedelta(minutes=(n - i) * 5)
        bars.append(_make_bar(ts, start_price + i * 0.1))
    return bars


def test_composite_score_zero_when_no_trades():
    assert _composite_score({"n_trades": 0, "total_return_pct": 10, "max_drawdown_pct": 5, "win_rate": 0.6}) == 0.0


def test_composite_score_zero_when_max_dd_zero():
    assert _composite_score({"n_trades": 10, "total_return_pct": 10, "max_drawdown_pct": 0, "win_rate": 0.6}) == 0.0


def test_composite_score_positive():
    score = _composite_score({"n_trades": 20, "total_return_pct": 30, "max_drawdown_pct": 10, "win_rate": 0.6})
    assert score > 0


def test_default_grid_contains_expected_keys():
    grid = _default_grid()
    assert "level_proximity_atr_mult" in grid
    assert "risk_per_trade_pct" in grid
    assert len(grid["level_proximity_atr_mult"]) == 3
    assert len(grid["risk_per_trade_pct"]) == 4


def test_simulate_returns_trades_and_report():
    bars = pd.DataFrame([
        {"timestamp": _make_bar(datetime.now(timezone.utc) - timedelta(minutes=i*5), 100 + i*0.1).timestamp,
         "open": 100 + i*0.1, "high": 100 + i*0.1 + 0.5, "low": 100 + i*0.1 - 0.5,
         "close": 100 + i*0.1, "volume": 100.0}
        for i in range(200)
    ])
    bars["session_date"] = bars["timestamp"].dt.date
    bars["poc_prev"] = 100.0
    bars["vah_prev"] = 105.0
    bars["val_prev"] = 95.0
    bars["close_prev"] = 99.5
    bars["in_gap_window"] = False
    bars["gap_direction"] = "none"
    bars["touched_close_prev"] = False
    bars["atr"] = 1.0
    bars["regime"] = "RANGE"
    bars["adx"] = 15.0
    bars["plus_di"] = 20.0
    bars["minus_di"] = 18.0

    settings = {
        "signals": {"level_proximity_atr_mult": 0.25, "breakout_confirm_atr_mult": 0.15},
        "risk": {"risk_per_trade_pct": 1.0},
        "symbol": "US500",
    }
    symbol_meta = {"minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}

    trades, snapshots = _simulate(bars, settings, symbol_meta, value_per_point_per_lot=1.0)
    assert isinstance(trades, list)
    assert isinstance(snapshots, list)


def test_failure_analysis_no_losses():
    from ctrader_bot.training.simulator import SimTrade
    trades = [
        SimTrade(
            entry_time=datetime.now(timezone.utc),
            exit_time=datetime.now(timezone.utc),
            symbol="US500", side="BUY", setup_tag="trend_pullback_poc",
            regime="TREND", entry_price=100.0, stop_price=99.0, target_price=102.0,
            volume=1.0, atr=1.0, pnl=10.0,
        )
    ]
    report = _failure_analysis(trades)
    assert "No losing trades" in report


def test_failure_analysis_with_losses():
    from ctrader_bot.training.simulator import SimTrade
    trades = [
        SimTrade(
            entry_time=datetime.now(timezone.utc),
            exit_time=datetime.now(timezone.utc),
            symbol="US500", side="BUY", setup_tag="range_fade_vah",
            regime="RANGE", entry_price=100.0, stop_price=99.0, target_price=98.0,
            volume=1.0, atr=1.0, pnl=-5.0, entry_data={"adx": 15.0},
        ),
        SimTrade(
            entry_time=datetime.now(timezone.utc),
            exit_time=datetime.now(timezone.utc),
            symbol="US500", side="SELL", setup_tag="range_fade_val",
            regime="RANGE", entry_price=100.0, stop_price=101.0, target_price=102.0,
            volume=1.0, atr=1.0, pnl=-3.0, entry_data={"adx": 14.0},
        ),
    ]
    report = _failure_analysis(trades)
    assert "Losing trades: 2" in report
    assert "range_fade_vah" in report
    assert "range_fade_val" in report
    assert "Entry Data Snapshots" in report


def test_include_live_blends_feedback_into_score():
    backtest = {
        "n_trades": 10, "win_rate": 0.4, "avg_r": 0.1,
        "total_return_pct": 2.0, "max_drawdown_pct": 2.0,
    }
    base_score = _composite_score(backtest)

    summary = {"by_setup_tag": {"good_setup": {"n": 10, "avg_r": 2.0}}}
    synth = _build_live_synthetic(summary, feedback_weight=3.0)
    blended = _blend_metrics(backtest, synth)
    blended_score = _composite_score(blended)

    assert blended_score > base_score
    assert blended["n_trades"] == 13  # 10 backtest + 3 synthetic (1 bucket × weight 3)


def test_include_live_ignored_without_feedback():
    backtest = {
        "n_trades": 10, "win_rate": 0.4, "avg_r": 0.1,
        "total_return_pct": 2.0, "max_drawdown_pct": 2.0,
    }
    synth = {"n": 0, "wins": 0, "r_sum": 0.0, "synth_return": 0.0}
    blended = _blend_metrics(backtest, synth)
    assert blended["n_trades"] == 10
    assert _composite_score(blended) == _composite_score(backtest)
