import numpy as np
import pandas as pd

from ctrader_bot.indicators.regime import Regime, classify_regime


def _bars_from_closes(closes: list[float]) -> pd.DataFrame:
    closes = np.array(closes, dtype=float)
    highs = closes + 0.3
    lows = closes - 0.3
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="5min", tz="UTC")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes}, index=idx)


def test_flat_choppy_series_stays_range():
    rng = np.random.default_rng(0)
    closes = 100 + rng.normal(0, 0.05, size=120).cumsum() * 0  # exactly flat
    closes = 100 + rng.uniform(-0.5, 0.5, size=120)
    df = _bars_from_closes(list(closes))
    vah = pd.Series(100.6, index=df.index)
    val = pd.Series(99.4, index=df.index)
    regimes = classify_regime(df, vah, val, adx_period=14, atr_median_lookback=20)
    assert regimes.iloc[-1] == Regime.RANGE


def test_sustained_directional_move_reaches_trend():
    n = 150
    closes = 100 + np.linspace(0, 30, n)  # steady, strong uptrend
    df = _bars_from_closes(list(closes))
    vah = pd.Series(101.0, index=df.index)
    val = pd.Series(99.0, index=df.index)
    regimes = classify_regime(
        df, vah, val,
        adx_trend_threshold=25, adx_range_threshold=20,
        trend_confirm_bars=6, atr_median_lookback=20,
    )
    assert Regime.TREND in set(regimes.iloc[-30:])


def test_breakout_reverts_to_range_on_failed_follow_through():
    flat = [100.0] * 40
    spike = [100 + i * 0.8 for i in range(1, 4)]
    back_inside = [100.2] * 40
    closes = flat + spike + back_inside
    df = _bars_from_closes(closes)
    vah = pd.Series(100.4, index=df.index)
    val = pd.Series(99.6, index=df.index)
    regimes = classify_regime(df, vah, val, atr_median_lookback=20, trend_confirm_bars=6)
    assert regimes.iloc[-1] == Regime.RANGE
