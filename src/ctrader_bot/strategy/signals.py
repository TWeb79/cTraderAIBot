"""Combines regime + volume-profile levels into entry signals.

Single source of truth used by both the backtest engine and the live runner
(evaluate_bar is the one function that decides "is there a signal here" —
never duplicated between backtest and live code paths).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from ctrader_bot.indicators.regime import Regime


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Signal:
    side: Side
    reason: str
    entry_price: float
    stop_price: float
    target_price: float
    regime: Regime


def local_direction_bullish(closes: pd.Series, lookback: int) -> bool | None:
    """Simple momentum direction over `lookback` bars: True=bullish, False=bearish, None=insufficient data."""
    if len(closes) <= lookback:
        return None
    return bool(closes.iloc[-1] > closes.iloc[-1 - lookback])


def evaluate_bar(
    row: pd.Series,
    recent_closes: pd.Series,
    atr: float,
    level_proximity_atr_mult: float = 0.25,
    breakout_confirm_atr_mult: float = 0.15,
    trend_direction_lookback: int = 20,
) -> Signal | None:
    """row must contain: close, regime, poc_prev, vah_prev, val_prev, close_prev,
    in_gap_window, gap_direction, touched_close_prev.

    recent_closes: closes up to and including this bar, used to infer local
    trend direction for gap-fill/breakout alignment checks.

    Returns at most one Signal for this bar, or None. Position management
    (whether a new signal is actually acted on given existing exposure) is
    the caller's responsibility (backtest engine / live runner), not this
    function's.
    """
    if atr is None or atr <= 0 or pd.isna(atr):
        return None

    regime = row["regime"]
    close = row["close"]
    poc_prev, vah_prev, val_prev, close_prev = row["poc_prev"], row["vah_prev"], row["val_prev"], row["close_prev"]
    if any(pd.isna(x) for x in (poc_prev, vah_prev, val_prev, close_prev)):
        return None  # no prior-session levels yet (e.g. first session in the dataset)

    trend_bullish = local_direction_bullish(recent_closes, trend_direction_lookback)

    # 1. NY-open gap-fill (time-limited, highest priority while active).
    if row.get("in_gap_window") and not row.get("touched_close_prev") and row.get("gap_direction") != "none":
        gap_dir = row["gap_direction"]  # "above" -> price above close_prev -> fill = move down -> SELL
        fill_side = Side.SELL if gap_dir == "above" else Side.BUY

        take_trade = False
        if regime == Regime.RANGE:
            take_trade = True
        elif regime == Regime.BREAKOUT:
            # only take if breakout direction agrees with the fill direction (doesn't fight it)
            breakout_bullish = close > vah_prev
            fights = (fill_side == Side.SELL and breakout_bullish) or (fill_side == Side.BUY and not breakout_bullish)
            take_trade = not fights
        elif regime == Regime.TREND:
            take_trade = trend_bullish is not None and (
                (fill_side == Side.BUY and trend_bullish) or (fill_side == Side.SELL and not trend_bullish)
            )

        if take_trade:
            if fill_side == Side.SELL:
                stop = close + max(atr * 0.5, abs(close - close_prev) * 0.25)
            else:
                stop = close - max(atr * 0.5, abs(close - close_prev) * 0.25)
            return Signal(side=fill_side, reason="ny_open_gap_fill", entry_price=close, stop_price=stop,
                          target_price=close_prev, regime=regime)

    # 2. Level reactions: proximity to VAH / VAL / POC.
    near = lambda level: abs(close - level) <= level_proximity_atr_mult * atr  # noqa: E731
    broke_above = close > vah_prev + breakout_confirm_atr_mult * atr
    broke_below = close < val_prev - breakout_confirm_atr_mult * atr
    measured_move = vah_prev - val_prev

    if regime == Regime.RANGE:
        if near(vah_prev) and close <= vah_prev:
            return Signal(Side.SELL, "range_fade_vah", close, vah_prev + atr * 0.5, poc_prev, regime)
        if near(val_prev) and close >= val_prev:
            return Signal(Side.BUY, "range_fade_val", close, val_prev - atr * 0.5, poc_prev, regime)

    elif regime in (Regime.BREAKOUT, Regime.TREND):
        if broke_above:
            return Signal(Side.BUY, "breakout_continuation_above_vah", close, vah_prev - atr * 0.5,
                          close + measured_move, regime)
        if broke_below:
            return Signal(Side.SELL, "breakout_continuation_below_val", close, val_prev + atr * 0.5,
                          close - measured_move, regime)
        if regime == Regime.TREND and near(poc_prev) and trend_bullish is not None:
            if trend_bullish:
                return Signal(Side.BUY, "trend_pullback_poc", close, poc_prev - atr * 0.75,
                              close + measured_move, regime)
            else:
                return Signal(Side.SELL, "trend_pullback_poc", close, poc_prev + atr * 0.75,
                              close - measured_move, regime)

    return None
