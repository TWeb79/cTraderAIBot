"""Market regime classification: RANGE / BREAKOUT / TREND.

TREND: ADX above threshold with sustained +DI/-DI separation.
BREAKOUT: price closes beyond the prior session's value area (VAH/VAL) while
  ATR is expanding relative to its rolling median — the transitional/trigger
  phase. Promotes to TREND once ADX/DI alignment persists for
  `trend_confirm_bars`; reverts to RANGE if price closes back inside the
  value area without follow-through (failed breakout).
RANGE: default state otherwise.
"""

from __future__ import annotations

from enum import Enum

import numpy as np
import pandas as pd


class Regime(str, Enum):
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    TREND = "TREND"


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx_di(df: pd.DataFrame, period: int) -> pd.DataFrame:
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    atr = wilder_atr(df, period)
    smoothed_plus_dm = plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    smoothed_minus_dm = minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    plus_di = 100 * smoothed_plus_dm / atr.replace(0, np.nan)
    minus_di = 100 * smoothed_minus_dm / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    return pd.DataFrame({"adx": adx, "plus_di": plus_di, "minus_di": minus_di, "atr": atr})


def classify_regime(
    df: pd.DataFrame,
    vah_prev: pd.Series,
    val_prev: pd.Series,
    adx_trend_threshold: float = 25,
    adx_range_threshold: float = 20,
    di_separation_min: float = 5,
    trend_confirm_bars: int = 6,
    atr_expansion_factor: float = 1.3,
    atr_median_lookback: int = 50,
    adx_period: int = 14,
) -> pd.Series:
    """Returns a pd.Series of Regime values aligned to df.index.

    vah_prev/val_prev: per-bar reference value-area levels (typically the
    prior session's VAH/VAL, forward-filled across the current session).
    """
    ind = adx_di(df, adx_period)
    atr_median = ind["atr"].rolling(atr_median_lookback, min_periods=max(5, atr_median_lookback // 5)).median()
    close = df["close"]

    trending_condition = (ind["adx"] > adx_trend_threshold) & ((ind["plus_di"] - ind["minus_di"]).abs() > di_separation_min)
    di_bullish = ind["plus_di"] > ind["minus_di"]
    atr_expanding = ind["atr"] > atr_expansion_factor * atr_median
    breaks_above = close > vah_prev
    breaks_below = close < val_prev
    inside_value_area = (close <= vah_prev) & (close >= val_prev)

    regimes: list[Regime] = []
    state = Regime.RANGE
    confirm_count = 0
    trend_direction_bullish: bool | None = None

    for i in range(len(df)):
        trending_now = bool(trending_condition.iloc[i]) if not pd.isna(trending_condition.iloc[i]) else False
        breakout_trigger = bool((breaks_above.iloc[i] or breaks_below.iloc[i]) and atr_expanding.iloc[i]) \
            if not pd.isna(atr_expanding.iloc[i]) else False
        back_inside = bool(inside_value_area.iloc[i])
        bullish = bool(di_bullish.iloc[i]) if not pd.isna(di_bullish.iloc[i]) else True

        if state == Regime.RANGE:
            if breakout_trigger:
                state = Regime.BREAKOUT
                confirm_count = 0
                trend_direction_bullish = bullish
            elif trending_now:
                state = Regime.TREND
                confirm_count = trend_confirm_bars
                trend_direction_bullish = bullish

        elif state == Regime.BREAKOUT:
            if back_inside:
                state = Regime.RANGE
                confirm_count = 0
            elif trending_now and bullish == trend_direction_bullish:
                confirm_count += 1
                if confirm_count >= trend_confirm_bars:
                    state = Regime.TREND
            else:
                confirm_count = 0

        elif state == Regime.TREND:
            if ind["adx"].iloc[i] < adx_range_threshold or back_inside:
                state = Regime.RANGE
                confirm_count = 0

        regimes.append(state)

    return pd.Series(regimes, index=df.index)
