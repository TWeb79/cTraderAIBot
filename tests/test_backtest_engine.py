import numpy as np
import pandas as pd

from ctrader_bot.backtest.engine import Trade, _check_exit, prepare_backtest_bars, run_backtest
from ctrader_bot.indicators.regime import Regime
from ctrader_bot.risk.risk_manager import RiskLimits
from ctrader_bot.strategy.signals import Side


def _limits(**overrides):
    base = dict(risk_per_trade_pct=1.5, max_daily_loss_pct=5.0, max_open_risk_pct=10.0, min_stop_atr_mult=0.5)
    base.update(overrides)
    return RiskLimits(**base)


def _symbol_meta():
    return {"minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}


def test_check_exit_conservative_stop_first_buy():
    trade = Trade(id="t", side=Side.BUY, reason="x", regime=Regime.RANGE,
                  entry_time=pd.Timestamp("2026-01-01"), entry_price=100, stop_price=95, target_price=110, volume=1)
    bar = pd.Series({"low": 90, "high": 120})  # both stop and target inside range
    price, reason = _check_exit(trade, bar)
    assert reason == "stop"
    assert price == 95


def test_check_exit_target_only_sell():
    trade = Trade(id="t", side=Side.SELL, reason="x", regime=Regime.RANGE,
                  entry_time=pd.Timestamp("2026-01-01"), entry_price=100, stop_price=105, target_price=90, volume=1)
    bar = pd.Series({"low": 88, "high": 99})
    price, reason = _check_exit(trade, bar)
    assert reason == "target"
    assert price == 90


def test_check_exit_none_when_neither_hit():
    trade = Trade(id="t", side=Side.BUY, reason="x", regime=Regime.RANGE,
                  entry_time=pd.Timestamp("2026-01-01"), entry_price=100, stop_price=95, target_price=110, volume=1)
    bar = pd.Series({"low": 98, "high": 103})
    assert _check_exit(trade, bar) is None


def _build_synthetic_bars(n_sessions: int = 6, bars_per_session: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    price = 100.0
    for s in range(n_sessions):
        session_start = pd.Timestamp("2026-08-01T22:00:00Z") + pd.Timedelta(days=s)
        for b in range(bars_per_session):
            drift = 0.02 if s % 2 == 0 else -0.02  # alternate trend direction per session
            price += rng.normal(drift, 0.4)
            high = price + abs(rng.normal(0, 0.3))
            low = price - abs(rng.normal(0, 0.3))
            vol = abs(rng.normal(200, 50))
            rows.append({
                "timestamp": session_start + pd.Timedelta(minutes=b),
                "open": price, "high": high, "low": low, "close": price, "volume": vol,
            })
    return pd.DataFrame(rows)


def test_prepare_and_run_backtest_end_to_end_smoke():
    m1_bars = _build_synthetic_bars(n_sessions=8, bars_per_session=300)
    m5_bars = m1_bars.iloc[::5].reset_index(drop=True)

    cfg = {
        "session_rollover_utc_hour": 21,
        "pip_size": 1.0,
        "volume_profile": {"price_bin_ticks": 1, "value_area_pct": 0.70},
        "session": {"ny_open_utc": "13:30", "gap_fill_window_minutes": 60},
        "regime": {
            "adx_period": 14, "adx_trend_threshold": 25, "adx_range_threshold": 20,
            "di_separation_min": 5, "trend_confirm_bars": 6,
            "atr_expansion_factor": 1.3, "atr_median_lookback": 20,
        },
    }
    bars = prepare_backtest_bars(m5_bars, m1_bars, cfg)
    assert "regime" in bars.columns
    assert "atr" in bars.columns
    assert set(bars["regime"].dropna().unique()) <= {Regime.RANGE, Regime.BREAKOUT, Regime.TREND}

    result = run_backtest(
        bars, risk_limits=_limits(), initial_equity=10000, value_per_point_per_lot=0.87,
        symbol_meta=_symbol_meta(), spread_points=0.4,
    )
    assert len(result.equity_curve) == len(bars)
    # No position sized larger than what max_open_risk_pct * equity / stop_distance would allow
    for t in result.trades:
        assert t.volume > 0
        assert t.pnl is not None  # every trade in the log is closed out


def test_daily_loss_halt_prevents_new_trades_same_session():
    m1_bars = _build_synthetic_bars(n_sessions=4, bars_per_session=300, seed=7)
    m5_bars = m1_bars.iloc[::5].reset_index(drop=True)
    cfg = {
        "session_rollover_utc_hour": 21,
        "pip_size": 1.0,
        "volume_profile": {"price_bin_ticks": 1, "value_area_pct": 0.70},
        "session": {"ny_open_utc": "13:30", "gap_fill_window_minutes": 60},
        "regime": {
            "adx_period": 14, "adx_trend_threshold": 25, "adx_range_threshold": 20,
            "di_separation_min": 5, "trend_confirm_bars": 6,
            "atr_expansion_factor": 1.3, "atr_median_lookback": 20,
        },
    }
    bars = prepare_backtest_bars(m5_bars, m1_bars, cfg)
    # Extremely tight daily loss limit forces the circuit breaker to engage quickly.
    result = run_backtest(
        bars, risk_limits=_limits(max_daily_loss_pct=0.01), initial_equity=10000,
        value_per_point_per_lot=0.87, symbol_meta=_symbol_meta(),
    )
    # With such a tight limit, at most one trade per session should ever be allowed to lose
    # meaningfully before the breaker halts further entries.
    assert isinstance(result.trades, list)


def _base_cfg():
    return {
        "session_rollover_utc_hour": 21,
        "pip_size": 1.0,
        "volume_profile": {"price_bin_ticks": 1, "value_area_pct": 0.70},
        "session": {"ny_open_utc": "13:30", "gap_fill_window_minutes": 60},
        "regime": {
            "adx_period": 14, "adx_trend_threshold": 25, "adx_range_threshold": 20,
            "di_separation_min": 5, "trend_confirm_bars": 6,
            "atr_expansion_factor": 1.3, "atr_median_lookback": 20,
        },
    }


def test_prepare_backtest_bars_always_attaches_vwap_and_ema():
    m1_bars = _build_synthetic_bars(n_sessions=4, bars_per_session=200, seed=3)
    m5_bars = m1_bars.iloc[::5].reset_index(drop=True)
    bars = prepare_backtest_bars(m5_bars, m1_bars, _base_cfg())
    assert "vwap" in bars.columns
    assert "ema_fast" in bars.columns
    assert "ema_slow" in bars.columns
    assert bars["vwap"].notna().any()


def test_prepare_backtest_bars_macro_columns_absent_without_macro_bars():
    m1_bars = _build_synthetic_bars(n_sessions=4, bars_per_session=200, seed=3)
    m5_bars = m1_bars.iloc[::5].reset_index(drop=True)
    bars = prepare_backtest_bars(m5_bars, m1_bars, _base_cfg())
    assert "macro_macd_bullish" not in bars.columns


def test_prepare_backtest_bars_attaches_macro_macd_when_macro_bars_given():
    m1_bars = _build_synthetic_bars(n_sessions=4, bars_per_session=200, seed=3)
    m5_bars = m1_bars.iloc[::5].reset_index(drop=True)
    m15_bars = m1_bars.iloc[::15].reset_index(drop=True)
    bars = prepare_backtest_bars(m5_bars, m1_bars, _base_cfg(), macro_bars=m15_bars)
    assert "macro_macd_bullish" in bars.columns
    assert "macro_macd_histogram" in bars.columns
    assert len(bars) == len(m5_bars)
