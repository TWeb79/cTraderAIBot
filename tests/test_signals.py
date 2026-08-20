import pandas as pd

from ctrader_bot.indicators.regime import Regime
from ctrader_bot.strategy.signals import Side, evaluate_bar


def _row(**kwargs) -> pd.Series:
    base = dict(
        close=100.0, regime=Regime.RANGE,
        poc_prev=95.0, vah_prev=100.0, val_prev=90.0, close_prev=98.0,
        in_gap_window=False, touched_close_prev=False, gap_direction="none",
    )
    base.update(kwargs)
    return pd.Series(base)


def _flat_recent_closes(value: float = 100.0, n: int = 25) -> pd.Series:
    return pd.Series([value] * n)


def _rising_recent_closes(n: int = 25) -> pd.Series:
    return pd.Series([100.0 + i * 0.5 for i in range(n)])


def _falling_recent_closes(n: int = 25) -> pd.Series:
    return pd.Series([100.0 - i * 0.5 for i in range(n)])


def test_ny_open_gap_fill_sell_in_range():
    row = _row(regime=Regime.RANGE, close=101.0, close_prev=98.0,
               in_gap_window=True, touched_close_prev=False, gap_direction="above")
    sig = evaluate_bar(row, _flat_recent_closes(101.0), atr=2.0)
    assert sig is not None
    assert sig.side == Side.SELL
    assert sig.reason == "ny_open_gap_fill"
    assert sig.target_price == 98.0


def test_no_gap_fill_once_already_touched():
    row = _row(regime=Regime.RANGE, close=101.0, close_prev=98.0,
               in_gap_window=True, touched_close_prev=True, gap_direction="above")
    sig = evaluate_bar(row, _flat_recent_closes(101.0), atr=2.0)
    assert sig is None or sig.reason != "ny_open_gap_fill"


def test_range_fade_at_vah():
    row = _row(regime=Regime.RANGE, close=99.7, vah_prev=100.0, poc_prev=95.0)
    sig = evaluate_bar(row, _flat_recent_closes(99.7), atr=2.0)
    assert sig is not None
    assert sig.side == Side.SELL
    assert sig.reason == "range_fade_vah"
    assert sig.target_price == 95.0


def test_range_fade_at_val():
    row = _row(regime=Regime.RANGE, close=90.3, val_prev=90.0, poc_prev=95.0)
    sig = evaluate_bar(row, _flat_recent_closes(90.3), atr=2.0)
    assert sig is not None
    assert sig.side == Side.BUY
    assert sig.reason == "range_fade_val"


def test_breakout_continuation_wins_over_conflicting_gap_fill():
    row = _row(regime=Regime.BREAKOUT, close=103.0, vah_prev=100.0, val_prev=90.0, close_prev=98.0,
               in_gap_window=True, touched_close_prev=False, gap_direction="above")
    sig = evaluate_bar(row, _flat_recent_closes(103.0), atr=2.0)
    assert sig is not None
    assert sig.side == Side.BUY
    assert sig.reason == "breakout_continuation_above_vah"


def test_trend_pullback_to_poc_bullish():
    row = _row(regime=Regime.TREND, close=95.2, poc_prev=95.0, vah_prev=100.0, val_prev=90.0)
    sig = evaluate_bar(row, _rising_recent_closes(), atr=2.0)
    assert sig is not None
    assert sig.side == Side.BUY
    assert sig.reason == "trend_pullback_poc"


def test_trend_pullback_to_poc_bearish():
    row = _row(regime=Regime.TREND, close=94.8, poc_prev=95.0, vah_prev=100.0, val_prev=90.0)
    sig = evaluate_bar(row, _falling_recent_closes(), atr=2.0)
    assert sig is not None
    assert sig.side == Side.SELL
    assert sig.reason == "trend_pullback_poc"


def test_no_signal_when_missing_prior_levels():
    row = _row(poc_prev=float("nan"))
    sig = evaluate_bar(row, _flat_recent_closes(), atr=2.0)
    assert sig is None


def test_no_signal_far_from_any_level_in_range():
    row = _row(regime=Regime.RANGE, close=95.0, vah_prev=100.0, val_prev=90.0, poc_prev=95.0)
    sig = evaluate_bar(row, _flat_recent_closes(95.0), atr=2.0)
    assert sig is None


# ── Bounce strategies (§15.9) — opt-in via enable_bounce_strategies ─────────

def test_bounce_strategies_disabled_by_default_even_near_vwap():
    # Far from VAH/VAL/POC so nothing else fires, but close to vwap — with
    # bounce strategies off (the default) this must stay a no-signal bar so
    # existing callers (backtest/live runner not yet opted in) are unaffected.
    row = _row(regime=Regime.RANGE, close=95.0, vah_prev=100.0, val_prev=90.0, poc_prev=94.0, vwap=95.1)
    sig = evaluate_bar(row, _rising_recent_closes(), atr=2.0)
    assert sig is None


def test_vwap_bounce_buy_when_bullish_and_near_vwap():
    row = _row(regime=Regime.RANGE, close=95.0, vah_prev=100.0, val_prev=90.0, poc_prev=94.0, vwap=95.1)
    sig = evaluate_bar(row, _rising_recent_closes(), atr=2.0, enable_bounce_strategies=True)
    assert sig is not None
    assert sig.side == Side.BUY
    assert sig.reason == "vwap_bounce"


def test_vwap_bounce_sell_when_bearish_and_near_vwap():
    row = _row(regime=Regime.RANGE, close=95.0, vah_prev=100.0, val_prev=90.0, poc_prev=94.0, vwap=94.9)
    sig = evaluate_bar(row, _falling_recent_closes(), atr=2.0, enable_bounce_strategies=True)
    assert sig is not None
    assert sig.side == Side.SELL
    assert sig.reason == "vwap_bounce"


def test_ema_bounce_when_no_vwap_column_present():
    row = _row(regime=Regime.RANGE, close=95.0, vah_prev=100.0, val_prev=90.0, poc_prev=94.0, ema_slow=95.05)
    sig = evaluate_bar(row, _rising_recent_closes(), atr=2.0, enable_bounce_strategies=True)
    assert sig is not None
    assert sig.side == Side.BUY
    assert sig.reason == "ema_bounce"


def test_bounce_strategies_noop_when_levels_missing():
    row = _row(regime=Regime.RANGE, close=95.0, vah_prev=100.0, val_prev=90.0, poc_prev=94.0)
    sig = evaluate_bar(row, _rising_recent_closes(), atr=2.0, enable_bounce_strategies=True)
    assert sig is None


# ── Macro confirmation (§15.3) — opt-in via require_macro_confirmation ──────

def test_macro_confirmation_blocks_disagreeing_signal():
    row = _row(regime=Regime.RANGE, close=90.3, val_prev=90.0, poc_prev=95.0, macro_macd_bullish=False)
    sig = evaluate_bar(row, _flat_recent_closes(90.3), atr=2.0, require_macro_confirmation=True)
    assert sig is None


def test_macro_confirmation_allows_agreeing_signal():
    row = _row(regime=Regime.RANGE, close=90.3, val_prev=90.0, poc_prev=95.0, macro_macd_bullish=True)
    sig = evaluate_bar(row, _flat_recent_closes(90.3), atr=2.0, require_macro_confirmation=True)
    assert sig is not None
    assert sig.side == Side.BUY


def test_macro_confirmation_is_noop_when_column_missing():
    row = _row(regime=Regime.RANGE, close=90.3, val_prev=90.0, poc_prev=95.0)
    sig = evaluate_bar(row, _flat_recent_closes(90.3), atr=2.0, require_macro_confirmation=True)
    assert sig is not None
