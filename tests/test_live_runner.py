import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from ctrader_bot.execution.live_runner import (
    _apply_trained_params,
    check_kill_switch,
    create_kill_switch,
    remove_kill_switch,
    run_one_cycle,
    run_live,
)
from ctrader_bot.indicators.regime import Regime
from ctrader_bot.journal.store import Journal
from ctrader_bot.mcp_client import Bar
from ctrader_bot.risk.risk_manager import RiskLimits, RiskManager
from ctrader_bot.strategy.signals import Side, Signal
from ctrader_bot.training.registry import ParameterRegistry


def _make_bar(ts: datetime, close: float, volume: float = 100.0) -> Bar:
    return Bar(timestamp=ts, open=close, high=close + 0.5, low=close - 0.5, close=close, volume=volume)


def _make_bars(n: int = 60, start_price: float = 100.0) -> list[Bar]:
    bars = []
    now = datetime.now(timezone.utc)
    for i in range(n):
        ts = now - timedelta(minutes=(n - i) * 5)
        bars.append(_make_bar(ts, start_price + i * 0.1))
    return bars


def _settings():
    return {
        "symbol": "US500",
        "timeframes": {"signal": "M5", "profile": "M1", "htf": "H1"},
        "session": {"ny_open_utc": "13:30", "gap_fill_window_minutes": 60},
        "volume_profile": {"value_area_pct": 0.70, "price_bin_ticks": 5},
        "regime": {
            "adx_period": 14, "adx_trend_threshold": 25, "adx_range_threshold": 20,
            "di_separation_min": 5, "trend_confirm_bars": 6, "atr_period": 14,
            "atr_expansion_factor": 1.3, "atr_median_lookback": 50,
        },
        "signals": {"level_proximity_atr_mult": 0.25, "breakout_confirm_atr_mult": 0.15},
        "risk": {"risk_per_trade_pct": 1.5, "max_daily_loss_pct": 5.0, "max_open_risk_pct": 10.0, "min_stop_atr_mult": 0.5},
        "backtest": {"spread_points": 0.4, "commission_per_lot": 0.0, "initial_equity": 10000},
        "execution": {"poll_interval_seconds": 15, "dry_run_default": True, "bars_for_context": 100},
    }


@pytest.fixture
def tmp_journal(tmp_path):
    db = str(tmp_path / "journal.sqlite3")
    return Journal(db)


@pytest.fixture
def risk_manager():
    limits = RiskLimits(
        risk_per_trade_pct=1.5,
        max_daily_loss_pct=5.0,
        max_open_risk_pct=10.0,
        min_stop_atr_mult=0.5,
    )
    return RiskManager(limits=limits)


@pytest.mark.asyncio
async def test_run_one_cycle_dry_run_skips_order(tmp_path, tmp_journal, risk_manager):
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(100)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = []
    mcp.place_market_order.return_value = {"positionId": 123}

    with patch("ctrader_bot.execution.live_runner.prepare_backtest_bars") as mock_prepare:
        signal_bars = pd.DataFrame([
            {"timestamp": b.timestamp, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
            for b in _make_bars(100)
        ])
        profile_bars = pd.DataFrame([
            {"timestamp": b.timestamp, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
            for b in _make_bars(300, start_price=100.0)
        ])
        mock_prepare.return_value = signal_bars

        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=True)

    mcp.place_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_run_one_cycle_no_signal_returns_early(tmp_path, tmp_journal, risk_manager):
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(10)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}

    with patch("ctrader_bot.execution.live_runner.prepare_backtest_bars") as mock_prepare:
        signal_bars = pd.DataFrame([
            {"timestamp": b.timestamp, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
            for b in _make_bars(10)
        ])
        mock_prepare.return_value = signal_bars

        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=False)

    mcp.place_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_run_one_cycle_daily_loss_prevents_order(tmp_path, tmp_journal, risk_manager):
    settings = _settings()
    risk_manager.start_new_session("2026-08-19", equity=10000.0)
    risk_manager.record_realized_pnl(-600.0)

    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(100)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}
    mcp.get_balance.return_value = {"equity": 9400.0, "balance": 9400.0}

    with patch("ctrader_bot.execution.live_runner.prepare_backtest_bars") as mock_prepare:
        signal_bars = pd.DataFrame([
            {"timestamp": b.timestamp, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
            for b in _make_bars(100)
        ])
        mock_prepare.return_value = signal_bars

        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=False)

    mcp.place_market_order.assert_not_called()


def _fixed_signal(reason: str = "range_fade_vah") -> Signal:
    return Signal(side=Side.BUY, reason=reason, entry_price=105.0, stop_price=104.0,
                  target_price=108.0, regime=Regime.RANGE)


@pytest.mark.asyncio
async def test_run_one_cycle_auto_disabled_skips_order(tmp_path, tmp_journal, risk_manager):
    """POST /api/auto/set {"enabled": false} must pause new live entries."""
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(100)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}

    signal_bars = pd.DataFrame([
        {"timestamp": b.timestamp, "close": b.close} for b in _make_bars(100)
    ])
    with patch("ctrader_bot.execution.live_runner.prepare_backtest_bars", return_value=signal_bars), \
         patch("ctrader_bot.execution.live_runner.evaluate_bar", return_value=_fixed_signal()), \
         patch("ctrader_bot.execution.live_runner.load_auto_control", return_value={"enabled": False}):
        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=False)

    mcp.place_market_order.assert_not_called()
    mcp.get_balance.assert_not_called()  # gated before any account/order work


@pytest.mark.asyncio
async def test_run_one_cycle_auto_no_control_file_is_unchanged(tmp_path, tmp_journal, risk_manager):
    """A missing control file (no dashboard running) must not change behavior:
    the signal proceeds to sizing exactly as before this feature existed."""
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(100)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = []  # no calibration data -> cannot size -> safe no-op

    signal_bars = pd.DataFrame([
        {"timestamp": b.timestamp, "close": b.close} for b in _make_bars(100)
    ])
    with patch("ctrader_bot.execution.live_runner.prepare_backtest_bars", return_value=signal_bars), \
         patch("ctrader_bot.execution.live_runner.evaluate_bar", return_value=_fixed_signal()), \
         patch("ctrader_bot.execution.live_runner.load_auto_control", return_value={}):
        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=False)

    # Reached past the auto-gate (proven by get_balance being called) even
    # though no control file / no strategy override was present.
    mcp.get_balance.assert_called_once()
    mcp.place_market_order.assert_not_called()  # no deals to size against


@pytest.mark.asyncio
async def test_run_one_cycle_auto_strategy_filters_unsupported_signal(tmp_path, tmp_journal, risk_manager):
    """Selecting a strategy in the dashboard must restrict which signal
    families the live loop acts on, not just what the analysis panel shows."""
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(100)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}

    signal_bars = pd.DataFrame([
        {"timestamp": b.timestamp, "close": b.close} for b in _make_bars(100)
    ])
    # "ny_gap_fill" strategy only enables the gap_fill family; range_fade_vah must be rejected.
    with patch("ctrader_bot.execution.live_runner.prepare_backtest_bars", return_value=signal_bars), \
         patch("ctrader_bot.execution.live_runner.evaluate_bar", return_value=_fixed_signal("range_fade_vah")), \
         patch("ctrader_bot.execution.live_runner.load_auto_control",
               return_value={"enabled": True, "strategy": "ny_gap_fill"}):
        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=False)

    mcp.place_market_order.assert_not_called()
    mcp.get_balance.assert_not_called()


def test_check_kill_switch(tmp_path):
    kill_path = tmp_path / ".kill_switch"
    with patch("ctrader_bot.execution.live_runner.KILL_SWITCH_PATH", str(kill_path)):
        assert check_kill_switch() is True
        create_kill_switch()
        assert check_kill_switch() is False
        remove_kill_switch()
        assert check_kill_switch() is True


@pytest.mark.asyncio
async def test_run_live_asserts_demo_account(tmp_path):
    settings = _settings()
    secrets = {
        "ctrader_mcp_url": "http://127.0.0.1:9876/mcp/",
        "ctrader_account_id": "48131263",
        "demo_mode": True,
    }
    journal = Journal(str(tmp_path / "journal.sqlite3"))

    mcp = AsyncMock()
    mcp.__aenter__.return_value = mcp
    mcp.__aexit__.return_value = None
    mcp.get_positions.return_value = []
    setattr(mcp, "assert_demo_account", AsyncMock(return_value=None))

    with patch("ctrader_bot.execution.live_runner.CTraderMCPClient", return_value=mcp):
        with patch("ctrader_bot.execution.live_runner.load_secrets", return_value=secrets):
            with patch("ctrader_bot.execution.live_runner.load_settings", return_value=settings):
                with patch("ctrader_bot.execution.live_runner.check_kill_switch", side_effect=[True, False]):
                    with patch("ctrader_bot.execution.live_runner.run_one_cycle") as mock_cycle:
                        await run_live(dry_run=True, symbol="US500")

    mock_cycle.assert_called_once()


def test_apply_trained_params_overrides_settings(tmp_path):
    reg = ParameterRegistry(tmp_path / "registry.json")
    reg.save_best_params(
        {
            "level_proximity_atr_mult": 0.3,
            "breakout_confirm_atr_mult": 0.2,
            "trend_direction_lookback": 15,
            "risk_per_trade_pct": 2.0,
            "min_stop_atr_mult": 0.75,
        },
        {"total_return_pct": 1.0},
        source="backtest",
    )
    settings = _settings()
    overrides = _apply_trained_params(settings, reg)

    assert settings["signals"]["level_proximity_atr_mult"] == 0.3
    assert settings["signals"]["trend_direction_lookback"] == 15
    assert settings["risk"]["risk_per_trade_pct"] == 2.0
    assert settings["risk"]["min_stop_atr_mult"] == 0.75
    assert any("trained" in o for o in overrides)
    # Non-managed keys are untouched.
    assert settings["symbol"] == "US500"


def test_apply_trained_params_empty_is_noop(tmp_path):
    reg = ParameterRegistry(tmp_path / "registry.json")
    settings = _settings()
    overrides = _apply_trained_params(settings, reg)
    assert overrides == []
    assert settings["signals"]["level_proximity_atr_mult"] == 0.25


@pytest.mark.asyncio
async def test_run_live_use_trained_params_flag(tmp_path):
    """--use-trained-params must load registry best params into settings."""
    reg = ParameterRegistry(tmp_path / "registry.json")
    reg.save_best_params(
        {"level_proximity_atr_mult": 0.3, "breakout_confirm_atr_mult": 0.2,
         "trend_direction_lookback": 15, "risk_per_trade_pct": 2.0, "min_stop_atr_mult": 0.75},
        {"total_return_pct": 1.0}, source="backtest",
    )
    settings = _settings()
    secrets = {
        "ctrader_mcp_url": "http://127.0.0.1:9876/mcp/",
        "ctrader_account_id": "48131263",
        "demo_mode": True,
    }
    journal = Journal(str(tmp_path / "journal.sqlite3"))

    mcp = AsyncMock()
    mcp.__aenter__.return_value = mcp
    mcp.__aexit__.return_value = None
    mcp.get_positions.return_value = []
    setattr(mcp, "assert_demo_account", AsyncMock(return_value=None))

    with patch("ctrader_bot.execution.live_runner.CTraderMCPClient") as mcp_cls:
        mcp_cls.return_value = mcp
        with patch("ctrader_bot.execution.live_runner.ParameterRegistry", return_value=reg):
            with patch("ctrader_bot.execution.live_runner.load_secrets", return_value=secrets):
                with patch("ctrader_bot.execution.live_runner.load_settings", return_value=settings):
                    with patch("ctrader_bot.execution.live_runner.check_kill_switch", side_effect=[True, False]):
                        with patch("ctrader_bot.execution.live_runner.run_one_cycle") as mock_cycle:
                            await run_live(dry_run=True, symbol="US500", use_trained_params=True)

    mock_cycle.assert_called_once()
    # The cycle must have received the trained params via settings.
    call_settings = mock_cycle.call_args.args[6]
    assert call_settings["signals"]["level_proximity_atr_mult"] == 0.3
    assert call_settings["risk"]["risk_per_trade_pct"] == 2.0
