"""Exponential moving average helper shared by the MACD/VWAP-bounce indicators
and any future indicator that needs a plain EMA (chart.js already renders a
client-side EMA for display; this is the server-side equivalent used for
strategy decisions, so the two must not silently diverge in formula).
"""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Standard exponential moving average, seeded by pandas' recursive
    definition (adjust=False matches the classic streaming EMA formula used
    by most charting platforms, and is what MACD is defined in terms of).
    """
    return series.ewm(span=period, adjust=False, min_periods=period).mean()
