"""Event-driven backtest engine. Shares strategy.signals.evaluate_bar and
risk.risk_manager.RiskManager with the live runner so backtest and live
behavior can't silently diverge.

Fills are simulated conservatively: within a bar, if both the stop and the
target are inside the bar's [low, high] range, the stop is assumed to hit
first (standard conservative assumption for OHLC-bar backtests, since we
don't have intra-bar tick sequencing).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pandas as pd

from ctrader_bot.indicators.regime import Regime, adx_di, classify_regime
from ctrader_bot.risk.risk_manager import RiskManager, compute_stop_distance
from ctrader_bot.strategy.levels import (
    attach_prior_session_levels,
    compute_ny_open_gap_state,
    compute_session_levels,
)
from ctrader_bot.strategy.signals import Side, evaluate_bar


@dataclass
class Trade:
    id: str
    side: Side
    reason: str
    regime: Regime
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    volume: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl: float | None = None


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["timestamp", "equity"]))


def prepare_backtest_bars(
    signal_bars: pd.DataFrame,
    profile_bars: pd.DataFrame,
    cfg: dict,
) -> pd.DataFrame:
    """signal_bars/profile_bars: DataFrames with columns timestamp, open, high, low, close, volume
    (signal_bars at cfg['timeframes']['signal'], profile_bars at cfg['timeframes']['profile']).

    Returns signal_bars enriched with: session_date, poc_prev/vah_prev/val_prev/close_prev,
    poc_pre_ny_prev/vah_pre_ny_prev/val_pre_ny_prev (Asia+Frankfurt portion of the
    prior session), poc_ny_prev/vah_ny_prev/val_ny_prev (NY portion), ny_open_price_prev,
    day_close_price_prev, in_gap_window/gap_direction/touched_close_prev, atr, regime.
    """
    rollover = cfg.get("session_rollover_utc_hour", 21)
    bin_size = cfg["volume_profile"]["price_bin_ticks"] * cfg.get("pip_size", 1.0)
    ny_open_utc = cfg.get("session", {}).get("ny_open_utc")

    session_levels = compute_session_levels(
        profile_bars, bin_size=bin_size, value_area_pct=cfg["volume_profile"]["value_area_pct"],
        session_rollover_utc_hour=rollover, ny_open_utc=ny_open_utc,
    )
    bars = attach_prior_session_levels(signal_bars, session_levels, session_rollover_utc_hour=rollover)
    bars = compute_ny_open_gap_state(
        bars, ny_open_utc=cfg["session"]["ny_open_utc"],
        gap_fill_window_minutes=cfg["session"]["gap_fill_window_minutes"],
    )

    ind = adx_di(bars, period=cfg["regime"]["adx_period"])
    bars["atr"] = ind["atr"]
    bars["regime"] = classify_regime(
        bars, bars["vah_prev"], bars["val_prev"],
        adx_trend_threshold=cfg["regime"]["adx_trend_threshold"],
        adx_range_threshold=cfg["regime"]["adx_range_threshold"],
        di_separation_min=cfg["regime"]["di_separation_min"],
        trend_confirm_bars=cfg["regime"]["trend_confirm_bars"],
        atr_expansion_factor=cfg["regime"]["atr_expansion_factor"],
        atr_median_lookback=cfg["regime"]["atr_median_lookback"],
        adx_period=cfg["regime"]["adx_period"],
    )
    return bars


def _check_exit(trade: Trade, bar: pd.Series) -> tuple[float, str] | None:
    if trade.side == Side.BUY:
        if bar["low"] <= trade.stop_price:
            return trade.stop_price, "stop"
        if bar["high"] >= trade.target_price:
            return trade.target_price, "target"
    else:
        if bar["high"] >= trade.stop_price:
            return trade.stop_price, "stop"
        if bar["low"] <= trade.target_price:
            return trade.target_price, "target"
    return None


def run_backtest(
    bars: pd.DataFrame,
    risk_limits,
    initial_equity: float,
    value_per_point_per_lot: float,
    symbol_meta: dict,
    spread_points: float = 0.4,
    commission_per_lot: float = 0.0,
    level_proximity_atr_mult: float = 0.25,
    breakout_confirm_atr_mult: float = 0.15,
    trend_direction_lookback: int = 20,
) -> BacktestResult:
    equity = initial_equity
    risk_manager = RiskManager(limits=risk_limits, day_start_equity=equity)
    open_trade: Trade | None = None
    trades: list[Trade] = []
    equity_points: list[tuple[pd.Timestamp, float]] = []
    current_session = None

    for i in range(len(bars)):
        row = bars.iloc[i]

        if row["session_date"] != current_session:
            current_session = row["session_date"]
            risk_manager.start_new_session(current_session, equity)

        if open_trade is not None:
            exit_info = _check_exit(open_trade, row)
            if exit_info is not None:
                exit_price, exit_reason = exit_info
                direction = 1 if open_trade.side == Side.BUY else -1
                gross = (exit_price - open_trade.entry_price) * direction * open_trade.volume * value_per_point_per_lot
                commission = commission_per_lot * open_trade.volume * 2  # entry + exit
                pnl = gross - commission
                equity += pnl
                open_trade.exit_time = row["timestamp"]
                open_trade.exit_price = exit_price
                open_trade.exit_reason = exit_reason
                open_trade.pnl = pnl
                risk_manager.register_closed_trade(open_trade.id, pnl)
                trades.append(open_trade)
                open_trade = None

        if open_trade is None and risk_manager.can_open_new_trade():
            recent_closes = bars["close"].iloc[max(0, i - trend_direction_lookback - 1): i + 1]
            atr_val = row["atr"]
            signal = evaluate_bar(
                row, recent_closes, atr_val,
                level_proximity_atr_mult=level_proximity_atr_mult,
                breakout_confirm_atr_mult=breakout_confirm_atr_mult,
                trend_direction_lookback=trend_direction_lookback,
            )
            if signal is not None:
                half_spread = spread_points / 2
                entry_price = signal.entry_price + half_spread if signal.side == Side.BUY else signal.entry_price - half_spread
                raw_stop_distance = abs(signal.entry_price - signal.stop_price)
                stop_distance = compute_stop_distance(atr_val, raw_stop_distance, risk_limits.min_stop_atr_mult)
                stop_price = entry_price - stop_distance if signal.side == Side.BUY else entry_price + stop_distance

                volume = risk_manager.size_trade(
                    equity=equity, entry_price=entry_price, stop_price=stop_price,
                    value_per_point_per_lot=value_per_point_per_lot,
                    min_volume=symbol_meta["minVolume"], max_volume=symbol_meta["maxVolume"],
                    volume_step=symbol_meta["volumeStep"],
                )
                if volume is not None:
                    trade_id = str(uuid.uuid4())
                    open_trade = Trade(
                        id=trade_id, side=signal.side, reason=signal.reason, regime=signal.regime,
                        entry_time=row["timestamp"], entry_price=entry_price, stop_price=stop_price,
                        target_price=signal.target_price, volume=volume,
                    )
                    risk_amount = stop_distance * volume * value_per_point_per_lot
                    risk_manager.register_open_trade(trade_id, risk_amount)

        unrealized = 0.0
        if open_trade is not None:
            direction = 1 if open_trade.side == Side.BUY else -1
            unrealized = (row["close"] - open_trade.entry_price) * direction * open_trade.volume * value_per_point_per_lot
        equity_points.append((row["timestamp"], equity + unrealized))

    if open_trade is not None:
        last_row = bars.iloc[-1]
        direction = 1 if open_trade.side == Side.BUY else -1
        gross = (last_row["close"] - open_trade.entry_price) * direction * open_trade.volume * value_per_point_per_lot
        pnl = gross - commission_per_lot * open_trade.volume * 2
        equity += pnl
        open_trade.exit_time = last_row["timestamp"]
        open_trade.exit_price = last_row["close"]
        open_trade.exit_reason = "end_of_data"
        open_trade.pnl = pnl
        trades.append(open_trade)

    equity_curve = pd.DataFrame(equity_points, columns=["timestamp", "equity"])
    return BacktestResult(trades=trades, equity_curve=equity_curve)
