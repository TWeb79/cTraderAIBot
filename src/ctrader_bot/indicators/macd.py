"""MACD (Moving Average Convergence Divergence) — used as an optional macro
(higher-timeframe) directional confirmation filter (implementationplan.md
§15.3), not as a standalone entry trigger. Kept as a plain, well-known
deterministic formula: no fitting, no lookahead.
"""

from __future__ import annotations

import pandas as pd

from ctrader_bot.indicators.moving_averages import ema


def macd(
    close: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """Returns a DataFrame (aligned to `close`'s index) with columns:
      macd       — fast EMA minus slow EMA
      signal     — EMA of the macd line
      histogram  — macd minus signal
      bullish    — bool, histogram > 0 (macd line above its signal line)
    """
    fast = ema(close, fast_period)
    slow = ema(close, slow_period)
    macd_line = fast - slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False, min_periods=signal_period).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
        "bullish": histogram > 0,
    })
