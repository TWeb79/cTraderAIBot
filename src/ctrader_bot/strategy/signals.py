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


def _macro_confirms(row: pd.Series, side: Side) -> bool:
    """True unless the macro (higher-timeframe) MACD column is present AND
    actively disagrees with `side`. Missing/NaN macro data (the common case —
    macro_bars is opt-in, see backtest.engine.prepare_backtest_bars) always
    passes, so this filter can never turn a signal off in a context where
    macro confirmation data simply wasn't supplied."""
    bullish = row.get("macro_macd_bullish")
    if bullish is None or (isinstance(bullish, float) and pd.isna(bullish)):
        return True
    return bool(bullish) if side == Side.BUY else not bool(bullish)


def evaluate_bar(
    row: pd.Series,
    recent_closes: pd.Series,
    atr: float,
    level_proximity_atr_mult: float = 0.25,
    breakout_confirm_atr_mult: float = 0.15,
    trend_direction_lookback: int = 20,
    enable_bounce_strategies: bool = False,
    bounce_proximity_atr_mult: float = 0.25,
    require_macro_confirmation: bool = False,
) -> Signal | None:
    """row must contain: close, regime, poc_prev, vah_prev, val_prev, close_prev,
    in_gap_window, gap_direction, touched_close_prev. Optionally: vwap,
    ema_fast, ema_slow (for bounce strategies) and macro_macd_bullish (for
    macro confirmation) — all default-off and safe to omit.

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

    def _confirmed(signal: Signal | None) -> Signal | None:
        if signal is None or not require_macro_confirmation:
            return signal
        return signal if _macro_confirms(row, signal.side) else None

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
            confirmed = _confirmed(Signal(side=fill_side, reason="ny_open_gap_fill", entry_price=close,
                                           stop_price=stop, target_price=close_prev, regime=regime))
            if confirmed is not None:
                return confirmed

    # 2. Level reactions: proximity to VAH / VAL / POC.
    near = lambda level: abs(close - level) <= level_proximity_atr_mult * atr  # noqa: E731
    broke_above = close > vah_prev + breakout_confirm_atr_mult * atr
    broke_below = close < val_prev - breakout_confirm_atr_mult * atr
    measured_move = vah_prev - val_prev

    if regime == Regime.RANGE:
        if near(vah_prev) and close <= vah_prev:
            confirmed = _confirmed(Signal(Side.SELL, "range_fade_vah", close, vah_prev + atr * 0.5, poc_prev, regime))
            if confirmed is not None:
                return confirmed
        if near(val_prev) and close >= val_prev:
            confirmed = _confirmed(Signal(Side.BUY, "range_fade_val", close, val_prev - atr * 0.5, poc_prev, regime))
            if confirmed is not None:
                return confirmed

    elif regime in (Regime.BREAKOUT, Regime.TREND):
        if broke_above:
            confirmed = _confirmed(Signal(Side.BUY, "breakout_continuation_above_vah", close, vah_prev - atr * 0.5,
                          close + measured_move, regime))
            if confirmed is not None:
                return confirmed
        if broke_below:
            confirmed = _confirmed(Signal(Side.SELL, "breakout_continuation_below_val", close, val_prev + atr * 0.5,
                          close - measured_move, regime))
            if confirmed is not None:
                return confirmed
        if regime == Regime.TREND and near(poc_prev) and trend_bullish is not None:
            if trend_bullish:
                confirmed = _confirmed(Signal(Side.BUY, "trend_pullback_poc", close, poc_prev - atr * 0.75,
                              close + measured_move, regime))
            else:
                confirmed = _confirmed(Signal(Side.SELL, "trend_pullback_poc", close, poc_prev + atr * 0.75,
                              close - measured_move, regime))
            if confirmed is not None:
                return confirmed

    # 3. VWAP / EMA dynamic support-resistance bounces (opt-in — implementationplan.md
    # §15.9). Treats vwap/ema_slow as a moving support (price above, pulling back
    # down to test it -> BUY) or resistance (price below, pulling up to test it ->
    # SELL) level, same "pullback in the direction of local momentum" logic as
    # trend_pullback_poc above, just for a broader set of regimes/levels.
    if enable_bounce_strategies and regime in (Regime.RANGE, Regime.TREND) and trend_bullish is not None:
        bounce_near = lambda level: level is not None and not pd.isna(level) and abs(close - level) <= bounce_proximity_atr_mult * atr  # noqa: E731
        for level_name, level_val in (("vwap_bounce", row.get("vwap")), ("ema_bounce", row.get("ema_slow"))):
            if not bounce_near(level_val):
                continue
            if trend_bullish:
                confirmed = _confirmed(Signal(Side.BUY, level_name, close, level_val - atr * 0.5,
                              close + measured_move, regime))
            else:
                confirmed = _confirmed(Signal(Side.SELL, level_name, close, level_val + atr * 0.5,
                              close - measured_move, regime))
            if confirmed is not None:
                return confirmed

    return None
