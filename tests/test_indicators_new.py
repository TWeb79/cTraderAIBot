"""Tests for the new indicators added in implementationplan.md §15.3/§15.9:
EMA, MACD, session VWAP."""

import numpy as np
import pandas as pd
import pytest

from ctrader_bot.indicators.macd import macd
from ctrader_bot.indicators.moving_averages import ema
from ctrader_bot.indicators.vwap import session_vwap


def test_ema_converges_toward_constant_series():
    s = pd.Series([100.0] * 50)
    out = ema(s, period=10)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_ema_leading_values_are_nan_until_min_periods():
    s = pd.Series(np.arange(20, dtype=float))
    out = ema(s, period=5)
    assert out.iloc[:4].isna().all()
    assert not pd.isna(out.iloc[4])


def test_macd_columns_and_bullish_flag_on_uptrend():
    closes = pd.Series(100 + np.linspace(0, 20, 100))
    out = macd(closes, fast_period=5, slow_period=10, signal_period=3)
    assert list(out.columns) == ["macd", "signal", "histogram", "bullish"]
    assert bool(out["bullish"].iloc[-1]) is True
    assert out["histogram"].iloc[-1] > 0


def test_macd_bearish_on_downtrend():
    closes = pd.Series(100 - np.linspace(0, 20, 100))
    out = macd(closes, fast_period=5, slow_period=10, signal_period=3)
    assert bool(out["bullish"].iloc[-1]) is False


def test_session_vwap_resets_at_session_boundary():
    # Two sessions of 3 bars each, rollover at hour 21 UTC -> session boundary
    # crossed between 20:xx and 22:xx bars.
    ts = pd.to_datetime([
        "2026-01-01 18:00", "2026-01-01 19:00", "2026-01-01 20:00",
        "2026-01-01 22:00", "2026-01-01 23:00", "2026-01-02 00:00",
    ], utc=True)
    df = pd.DataFrame({
        "timestamp": ts,
        "high": [101, 102, 103, 200, 201, 202],
        "low": [99, 100, 101, 198, 199, 200],
        "close": [100, 101, 102, 199, 200, 201],
        "volume": [10, 10, 10, 10, 10, 10],
    })
    out = session_vwap(df, session_rollover_utc_hour=21)
    # Second session's VWAP should be centered near ~200, not dragged down by
    # the first session's ~100-level prices.
    assert out.iloc[-1] > 195
    assert out.iloc[2] < 105


def test_session_vwap_handles_zero_volume_without_raising():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-01 18:00", "2026-01-01 19:00"], utc=True),
        "high": [101, 102], "low": [99, 100], "close": [100, 101], "volume": [0, 0],
    })
    out = session_vwap(df)
    assert len(out) == 2
