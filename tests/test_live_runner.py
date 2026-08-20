import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from ctrader_bot.execution.live_runner import (
    _apply_trained_params,
    _execute_trade,
    _spot_price_for_side,
    check_kill_switch,
    consume_manual_trade_request,
    create_kill_switch,
    main,
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


@pytest.mark.asyncio
async def test_run_one_cycle_manual_trade_request_is_executed(tmp_path, tmp_journal, risk_manager):
    """A pending manual "execute predicted trade" request from the dashboard
    must be placed through the same sizing/order pipeline as an automated
    signal, independent of whatever evaluate_bar() decides this cycle."""
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(100)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = [
        {"symbolName": "US500", "pips": 100.0, "filledVolume": 1.0, "grossProfit": 100.0},
    ]
    mcp.place_market_order.return_value = {"positionId": 999}
    mcp.get_positions.return_value = []  # closes on first poll

    signal_bars = pd.DataFrame([
        {"timestamp": b.timestamp, "close": b.close} for b in _make_bars(100)
    ])
    manual_request = {
        "direction": "LONG", "entry": 105.0, "stop": 104.0, "target": 108.0,
        "reason": "manual-dashboard",
    }
    with patch("ctrader_bot.execution.live_runner.prepare_backtest_bars", return_value=signal_bars), \
         patch("ctrader_bot.execution.live_runner.evaluate_bar", return_value=None), \
         patch("ctrader_bot.execution.live_runner.consume_manual_trade_request", return_value=manual_request), \
         patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=False)

    mcp.place_market_order.assert_called_once()
    order_args = mcp.place_market_order.call_args.kwargs
    assert order_args["side"] == "buy"
    assert order_args["symbolName"] == "US500"


@pytest.mark.asyncio
async def test_run_one_cycle_manual_trade_request_respects_dry_run(tmp_path, tmp_journal, risk_manager):
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(100)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = [
        {"symbolName": "US500", "pips": 100.0, "filledVolume": 1.0, "grossProfit": 100.0},
    ]

    signal_bars = pd.DataFrame([
        {"timestamp": b.timestamp, "close": b.close} for b in _make_bars(100)
    ])
    manual_request = {
        "direction": "SHORT", "entry": 105.0, "stop": 106.0, "target": 102.0,
        "reason": "manual-dashboard",
    }
    with patch("ctrader_bot.execution.live_runner.prepare_backtest_bars", return_value=signal_bars), \
         patch("ctrader_bot.execution.live_runner.evaluate_bar", return_value=None), \
         patch("ctrader_bot.execution.live_runner.consume_manual_trade_request", return_value=manual_request):
        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=True)

    mcp.place_market_order.assert_not_called()


def test_consume_manual_trade_request_reads_and_deletes_file(tmp_path):
    req_path = tmp_path / ".manual_trade_request.json"
    req_path.write_text(
        '{"direction": "LONG", "entry": 105.0, "stop": 104.0, "target": 108.0, "reason": "manual-dashboard"}'
    )
    with patch("ctrader_bot.execution.live_runner.MANUAL_TRADE_REQUEST_PATH", str(req_path)):
        result = consume_manual_trade_request()
        assert result["direction"] == "LONG"
        assert result["target"] == 108.0
        assert not req_path.exists()  # consumed exactly once
        assert consume_manual_trade_request() is None


@pytest.mark.parametrize("bad_payload", [
    '{"direction": "SIDEWAYS", "entry": 1, "stop": 1, "target": 1}',  # bad direction
    '{"direction": "LONG", "entry": "not-a-number", "stop": 1, "target": 1}',  # bad type
    '{"direction": "LONG", "entry": 1, "stop": 1}',  # missing target
    'not json at all',
])
def test_consume_manual_trade_request_rejects_malformed(tmp_path, bad_payload):
    req_path = tmp_path / ".manual_trade_request.json"
    req_path.write_text(bad_payload)
    with patch("ctrader_bot.execution.live_runner.MANUAL_TRADE_REQUEST_PATH", str(req_path)):
        assert consume_manual_trade_request() is None


def test_consume_manual_trade_request_missing_file_returns_none(tmp_path):
    req_path = tmp_path / "does-not-exist.json"
    with patch("ctrader_bot.execution.live_runner.MANUAL_TRADE_REQUEST_PATH", str(req_path)):
        assert consume_manual_trade_request() is None


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
                # [outer-connect=True, inner-cycle=True, inner-stop=False, outer-stop=False] —
                # run_live() now checks the kill switch both around the MCP
                # connection (reconnect loop) and around each cycle.
                with patch("ctrader_bot.execution.live_runner.check_kill_switch",
                           side_effect=[True, True, False, False]):
                    with patch("ctrader_bot.execution.live_runner.run_one_cycle") as mock_cycle:
                        with patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
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
                    with patch("ctrader_bot.execution.live_runner.check_kill_switch",
                               side_effect=[True, True, False, False]):
                        with patch("ctrader_bot.execution.live_runner.run_one_cycle") as mock_cycle:
                            with patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
                                await run_live(dry_run=True, symbol="US500", use_trained_params=True)

    mock_cycle.assert_called_once()
    # The cycle must have received the trained params via settings.
    call_settings = mock_cycle.call_args.args[6]
    assert call_settings["signals"]["level_proximity_atr_mult"] == 0.3
    assert call_settings["risk"]["risk_per_trade_pct"] == 2.0


# ── _execute_trade: fixed RR / margin-% sizing / trailing stop / opened_at ──

def _symbol_details():
    return {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}


@pytest.mark.asyncio
async def test_execute_trade_fixed_rr_overrides_target(tmp_journal, risk_manager):
    settings = _settings()
    settings["risk"]["enforce_fixed_rr"] = True
    settings["risk"]["target_rr_ratio"] = 3.0

    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = [
        {"symbolName": "US500", "pips": 100.0, "filledVolume": 1.0, "grossProfit": 100.0},
    ]
    mcp.place_market_order.return_value = {"positionId": 555}
    mcp.get_positions.return_value = []  # closes on first poll

    with patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
        await _execute_trade(
            mcp, tmp_journal, risk_manager, "US500", Side.BUY,
            entry_price=100.0, raw_stop_price=95.0, target_price=105.0,
            reason="test_fixed_rr", regime="RANGE", atr_val=1.0, settings=settings,
            symbol_details=_symbol_details(), dry_run=False, registry=None,
        )

    order_args = mcp.place_market_order.call_args.kwargs
    # stop_distance floor = max(raw=5.0, min_stop_atr_mult(0.5)*atr(1.0)=0.5) = 5.0
    # fixed RR target = entry + stop_offset(5.0) * ratio(3.0) -> tp distance = 15 -> pips = 1500
    assert order_args["takeProfitPips"] == pytest.approx(1500.0)


@pytest.mark.asyncio
async def test_execute_trade_without_fixed_rr_keeps_signal_target(tmp_journal, risk_manager):
    settings = _settings()  # enforce_fixed_rr not set -> defaults off
    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = [
        {"symbolName": "US500", "pips": 100.0, "filledVolume": 1.0, "grossProfit": 100.0},
    ]
    mcp.place_market_order.return_value = {"positionId": 556}
    mcp.get_positions.return_value = []

    with patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
        await _execute_trade(
            mcp, tmp_journal, risk_manager, "US500", Side.BUY,
            entry_price=100.0, raw_stop_price=95.0, target_price=105.0,
            reason="test_no_fixed_rr", regime="RANGE", atr_val=1.0, settings=settings,
            symbol_details=_symbol_details(), dry_run=False, registry=None,
        )

    order_args = mcp.place_market_order.call_args.kwargs
    # unchanged signal target of 105 -> tp distance 5 -> pips = 500
    assert order_args["takeProfitPips"] == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_execute_trade_margin_pct_caps_volume(tmp_journal, risk_manager):
    settings = _settings()
    settings["risk"]["position_sizing_mode"] = "margin_pct"
    settings["risk"]["margin_pct_of_free_margin"] = 5.0

    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0, "freeMargin": 1000.0}
    mcp.get_deals.return_value = [
        {"symbolName": "US500", "pips": 100.0, "filledVolume": 1.0, "grossProfit": 100.0},
    ]
    mcp.calculate_margin.return_value = {"margin": 500.0}
    mcp.place_market_order.return_value = {"positionId": 557}
    mcp.get_positions.return_value = []

    with patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
        await _execute_trade(
            mcp, tmp_journal, risk_manager, "US500", Side.BUY,
            entry_price=100.0, raw_stop_price=95.0, target_price=105.0,
            reason="test_margin_pct", regime="RANGE", atr_val=1.0, settings=settings,
            symbol_details=_symbol_details(), dry_run=False, registry=None,
        )

    order_args = mcp.place_market_order.call_args.kwargs
    # risk_pct volume would be ~30 lots; margin cap = (1000*5%)/500 = 0.1 lot -> the smaller wins
    assert order_args["volume"] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_execute_trade_margin_pct_falls_back_to_risk_pct_when_margin_lookup_fails(tmp_journal, risk_manager):
    settings = _settings()
    settings["risk"]["position_sizing_mode"] = "margin_pct"

    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0, "freeMargin": 1000.0}
    mcp.get_deals.return_value = [
        {"symbolName": "US500", "pips": 100.0, "filledVolume": 1.0, "grossProfit": 100.0},
    ]
    mcp.calculate_margin.side_effect = Exception("mcp error")
    mcp.place_market_order.return_value = {"positionId": 558}
    mcp.get_positions.return_value = []

    with patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
        await _execute_trade(
            mcp, tmp_journal, risk_manager, "US500", Side.BUY,
            entry_price=100.0, raw_stop_price=95.0, target_price=105.0,
            reason="test_margin_pct_fallback", regime="RANGE", atr_val=1.0, settings=settings,
            symbol_details=_symbol_details(), dry_run=False, registry=None,
        )

    # No crash, and the risk_pct-sized volume (~30 lots) is used unchanged.
    order_args = mcp.place_market_order.call_args.kwargs
    assert order_args["volume"] == pytest.approx(30.0, abs=0.01)


@pytest.mark.asyncio
async def test_execute_trade_records_opened_at_and_pnl_in_journal(tmp_journal, risk_manager):
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = [
        {"symbolName": "US500", "pips": 100.0, "filledVolume": 1.0, "grossProfit": 100.0},
    ]
    mcp.place_market_order.return_value = {"positionId": 559}
    mcp.get_positions.return_value = []

    with patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
        await _execute_trade(
            mcp, tmp_journal, risk_manager, "US500", Side.BUY,
            entry_price=100.0, raw_stop_price=95.0, target_price=105.0,
            reason="test_journal", regime="RANGE", atr_val=1.0, settings=settings,
            symbol_details=_symbol_details(), dry_run=False, registry=None,
        )

    trades = tmp_journal.get_trades()
    assert len(trades) == 1
    assert trades[0]["opened_at"] is not None
    assert trades[0]["reflection"]["pnl"] == pytest.approx(100.0)
    assert trades[0]["decision"] is not None


@pytest.mark.asyncio
async def test_execute_trade_trailing_stop_locks_profit_and_amends_position(tmp_journal, risk_manager):
    settings = _settings()
    settings["risk"]["trailing_stop"] = {
        "enabled": True, "trigger_pips": 3.0, "lock_pips": 1.4,
        "tp_extend_trigger_pips": 5.0, "tp_extend_pips": 5.0, "sl_trail_distance_pips": 3.0,
    }

    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = [
        {"symbolName": "US500", "pips": 100.0, "filledVolume": 1.0, "grossProfit": 100.0},
    ]
    mcp.place_market_order.return_value = {"positionId": 777}
    # First poll: still open (trailing logic runs); second poll: closed.
    mcp.get_positions.side_effect = [[{"id": 777}], []]
    mcp.get_spot_prices.return_value = {"bid": 100.5, "ask": 100.6}

    with patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
        await _execute_trade(
            mcp, tmp_journal, risk_manager, "US500", Side.BUY,
            entry_price=100.0, raw_stop_price=95.0, target_price=110.0,
            reason="test_trailing", regime="RANGE", atr_val=1.0, settings=settings,
            symbol_details=_symbol_details(), dry_run=False, registry=None,
        )

    mcp.amend_position.assert_called()
    amend_kwargs = mcp.amend_position.call_args.kwargs
    # profit_pips = (100.5-100)/0.01 = 50 >= trigger(3) -> lock at entry + 1.4*pip = 100.014
    assert amend_kwargs["stop_loss"] == pytest.approx(100.014)


@pytest.mark.asyncio
async def test_execute_trade_trailing_stop_disabled_by_default_no_amend_call(tmp_journal, risk_manager):
    settings = _settings()  # trailing_stop not configured -> off
    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = [
        {"symbolName": "US500", "pips": 100.0, "filledVolume": 1.0, "grossProfit": 100.0},
    ]
    mcp.place_market_order.return_value = {"positionId": 778}
    mcp.get_positions.side_effect = [[{"id": 778}], []]
    mcp.get_spot_prices.return_value = {"bid": 100.5, "ask": 100.6}

    with patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
        await _execute_trade(
            mcp, tmp_journal, risk_manager, "US500", Side.BUY,
            entry_price=100.0, raw_stop_price=95.0, target_price=110.0,
            reason="test_no_trailing", regime="RANGE", atr_val=1.0, settings=settings,
            symbol_details=_symbol_details(), dry_run=False, registry=None,
        )

    mcp.amend_position.assert_not_called()
    mcp.get_spot_prices.assert_not_called()


def test_spot_price_for_side_prefers_bid_ask():
    assert _spot_price_for_side({"bid": 100.1, "ask": 100.2}, Side.BUY) == 100.1
    assert _spot_price_for_side({"bid": 100.1, "ask": 100.2}, Side.SELL) == 100.2


def test_spot_price_for_side_falls_back_to_generic_fields():
    assert _spot_price_for_side({"price": 99.9}, Side.BUY) == 99.9
    assert _spot_price_for_side({"last": 99.8}, Side.SELL) == 99.8


def test_spot_price_for_side_returns_none_when_unusable():
    assert _spot_price_for_side({}, Side.BUY) is None
    assert _spot_price_for_side("not a dict", Side.BUY) is None


# ── "fix everything which stops it from automatically trading" batch ───────

@pytest.mark.asyncio
async def test_run_one_cycle_starts_new_session_when_session_date_changes(tmp_path, tmp_journal, risk_manager):
    """Daily-loss circuit breaker regression: risk_manager.day_start_equity
    must actually get set during live trading. Previously start_new_session
    was only ever called from backtest/engine.py's run_backtest(), so in
    live trading day_start_equity stayed 0.0 forever and
    record_realized_pnl()'s `if self.day_start_equity > 0` guard meant
    halted_today could never trip — the breaker was silently dead."""
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(100)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}
    mcp.get_balance.return_value = {"equity": 12345.0, "balance": 12345.0}

    signal_bars = pd.DataFrame([
        {"timestamp": b.timestamp, "close": b.close, "session_date": "2026-08-20"} for b in _make_bars(100)
    ])
    assert risk_manager.current_session_date is None
    with patch("ctrader_bot.execution.live_runner.prepare_backtest_bars", return_value=signal_bars), \
         patch("ctrader_bot.execution.live_runner.evaluate_bar", return_value=None):
        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=False)

    assert risk_manager.current_session_date == "2026-08-20"
    assert risk_manager.day_start_equity == pytest.approx(12345.0)
    mcp.get_balance.assert_called_once()


@pytest.mark.asyncio
async def test_run_one_cycle_does_not_restart_session_when_unchanged(tmp_path, tmp_journal, risk_manager):
    """A session already started this cycle (same session_date) must not be
    re-started — that would reset realized_pnl_today/halted_today and quietly
    re-open the daily-loss breaker mid-session."""
    settings = _settings()
    risk_manager.start_new_session("2026-08-20", equity=9999.0)
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(100)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}

    signal_bars = pd.DataFrame([
        {"timestamp": b.timestamp, "close": b.close, "session_date": "2026-08-20"} for b in _make_bars(100)
    ])
    with patch("ctrader_bot.execution.live_runner.prepare_backtest_bars", return_value=signal_bars), \
         patch("ctrader_bot.execution.live_runner.evaluate_bar", return_value=None):
        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=False)

    mcp.get_balance.assert_not_called()
    assert risk_manager.day_start_equity == pytest.approx(9999.0)


@pytest.mark.asyncio
async def test_run_one_cycle_missing_session_date_is_noop_unchanged(tmp_path, tmp_journal, risk_manager):
    """Bars without a 'session_date' column (e.g. mocked/stubbed in other
    tests, or a future pipeline change) must degrade to exactly the old
    behavior: no session (re)start, no extra get_balance call."""
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(100)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}

    signal_bars = pd.DataFrame([
        {"timestamp": b.timestamp, "close": b.close} for b in _make_bars(100)
    ])
    with patch("ctrader_bot.execution.live_runner.prepare_backtest_bars", return_value=signal_bars), \
         patch("ctrader_bot.execution.live_runner.evaluate_bar", return_value=None):
        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=False)

    mcp.get_balance.assert_not_called()
    assert risk_manager.current_session_date is None


@pytest.mark.asyncio
async def test_execute_trade_uses_vpp_fallback_when_no_deal_history(tmp_journal, risk_manager):
    """A fresh/reset demo account with zero historical closed deals for the
    symbol must not be permanently blocked from its first trade when an
    explicit risk.value_per_point_per_lot_fallback is configured."""
    settings = _settings()
    settings["risk"]["value_per_point_per_lot_fallback"] = 0.5

    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = []  # no historical deals for this symbol
    mcp.place_market_order.return_value = {"positionId": 900}
    mcp.get_positions.return_value = []

    with patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
        await _execute_trade(
            mcp, tmp_journal, risk_manager, "US500", Side.BUY,
            entry_price=100.0, raw_stop_price=95.0, target_price=105.0,
            reason="test_vpp_fallback", regime="RANGE", atr_val=1.0, settings=settings,
            symbol_details=_symbol_details(), dry_run=False, registry=None,
        )

    mcp.place_market_order.assert_called_once()


@pytest.mark.asyncio
async def test_execute_trade_without_fallback_and_no_deals_still_refuses(tmp_journal, risk_manager):
    """Default behavior (no fallback configured) must be unchanged: refuse
    to guess, no order placed."""
    settings = _settings()  # value_per_point_per_lot_fallback not set -> None
    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = []

    await _execute_trade(
        mcp, tmp_journal, risk_manager, "US500", Side.BUY,
        entry_price=100.0, raw_stop_price=95.0, target_price=105.0,
        reason="test_no_fallback", regime="RANGE", atr_val=1.0, settings=settings,
        symbol_details=_symbol_details(), dry_run=False, registry=None,
    )

    mcp.place_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_run_live_retries_after_mcp_connection_failure(tmp_path):
    """A failed/dropped MCP connection must not crash the process — run_live
    should log it, wait, and retry the connection instead. Previously the
    `async with CTraderMCPClient(...)` was outside any try/except, so a
    connection failure (e.g. this process starting before the cTrader
    desktop app is ready) crashed the whole process uncaught."""
    settings = _settings()
    secrets = {
        "ctrader_mcp_url": "http://127.0.0.1:9876/mcp/",
        "ctrader_account_id": "1",
        "demo_mode": False,
    }

    good_mcp = AsyncMock()
    good_mcp.__aenter__.return_value = good_mcp
    good_mcp.__aexit__.return_value = None
    good_mcp.get_positions.return_value = []

    class _FailingCM:
        async def __aenter__(self):
            raise ConnectionError("mcp unavailable")

        async def __aexit__(self, *exc):
            return False

    call_count = {"n": 0}

    def _client_factory(url):
        call_count["n"] += 1
        return _FailingCM() if call_count["n"] == 1 else good_mcp

    with patch("ctrader_bot.execution.live_runner.CTraderMCPClient", side_effect=_client_factory):
        with patch("ctrader_bot.execution.live_runner.load_secrets", return_value=secrets):
            with patch("ctrader_bot.execution.live_runner.load_settings", return_value=settings):
                with patch("ctrader_bot.execution.live_runner.check_kill_switch",
                           side_effect=[True, True, True, False, False]):
                    with patch("ctrader_bot.execution.live_runner.run_one_cycle") as mock_cycle:
                        with patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
                            await run_live(dry_run=True, symbol="US500")

    assert call_count["n"] == 2  # first connection attempt failed, second succeeded
    mock_cycle.assert_called_once()


# ── main(): config.yaml execution.dry_run_default is no longer dead config ─

def _run_main_capturing_run_live(argv, config_settings):
    captured = {}

    async def fake_run_live(**kwargs):
        captured.update(kwargs)

    with patch.object(sys, "argv", ["run_live.py", *argv]), \
         patch("ctrader_bot.execution.live_runner.run_live", side_effect=fake_run_live), \
         patch("ctrader_bot.execution.live_runner.SETTINGS", config_settings), \
         patch("ctrader_bot.execution.live_runner.remove_kill_switch"):
        main()
    return captured


def test_main_no_flags_uses_config_dry_run_default_true():
    captured = _run_main_capturing_run_live([], {"execution": {"dry_run_default": True}})
    assert captured["dry_run"] is True


def test_main_no_flags_uses_config_dry_run_default_false():
    captured = _run_main_capturing_run_live([], {"execution": {"dry_run_default": False}})
    assert captured["dry_run"] is False


def test_main_live_flag_overrides_config_dry_run_default_true():
    captured = _run_main_capturing_run_live(["--live"], {"execution": {"dry_run_default": True}})
    assert captured["dry_run"] is False


def test_main_dry_run_flag_overrides_config_dry_run_default_false():
    captured = _run_main_capturing_run_live(["--dry-run"], {"execution": {"dry_run_default": False}})
    assert captured["dry_run"] is True


def test_main_dry_run_and_live_together_is_an_error():
    with patch.object(sys, "argv", ["run_live.py", "--dry-run", "--live"]), \
         patch("ctrader_bot.execution.live_runner.SETTINGS", {"execution": {}}):
        with pytest.raises(SystemExit):
            main()


# ── Live-cycle diagnostics ("why isn't it trading?") ───────────────────────

def _status_path(tmp_path):
    return str(tmp_path / ".last_cycle_status.json")


@pytest.mark.asyncio
async def test_run_one_cycle_writes_kill_switch_status(tmp_path, tmp_journal, risk_manager):
    status_path = _status_path(tmp_path)
    with patch("ctrader_bot.execution.live_runner.LAST_CYCLE_STATUS_PATH", status_path), \
         patch("ctrader_bot.execution.live_runner.check_kill_switch", return_value=False):
        await run_one_cycle(AsyncMock(), tmp_journal, risk_manager, "US500", "M5", "M1", _settings())

    data = json.loads(Path(status_path).read_text())
    assert data["outcome"] == "kill_switch"


@pytest.mark.asyncio
async def test_run_one_cycle_writes_no_signal_status(tmp_path, tmp_journal, risk_manager):
    status_path = _status_path(tmp_path)
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(10)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}

    signal_bars = pd.DataFrame([
        {"timestamp": b.timestamp, "close": b.close} for b in _make_bars(10)
    ])
    with patch("ctrader_bot.execution.live_runner.LAST_CYCLE_STATUS_PATH", status_path), \
         patch("ctrader_bot.execution.live_runner.prepare_backtest_bars", return_value=signal_bars):
        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=False)

    data = json.loads(Path(status_path).read_text())
    assert data["outcome"] == "no_signal"


@pytest.mark.asyncio
async def test_run_one_cycle_writes_auto_disabled_status(tmp_path, tmp_journal, risk_manager):
    status_path = _status_path(tmp_path)
    settings = _settings()
    mcp = AsyncMock()
    mcp.get_trendbars.return_value = _make_bars(100)
    mcp.get_symbol_details.return_value = {"pipSize": 0.01, "minVolume": 0.01, "maxVolume": 100, "volumeStep": 0.01}

    signal_bars = pd.DataFrame([
        {"timestamp": b.timestamp, "close": b.close} for b in _make_bars(100)
    ])
    with patch("ctrader_bot.execution.live_runner.LAST_CYCLE_STATUS_PATH", status_path), \
         patch("ctrader_bot.execution.live_runner.prepare_backtest_bars", return_value=signal_bars), \
         patch("ctrader_bot.execution.live_runner.evaluate_bar", return_value=_fixed_signal()), \
         patch("ctrader_bot.execution.live_runner.load_auto_control", return_value={"enabled": False}):
        await run_one_cycle(mcp, tmp_journal, risk_manager, "US500", "M5", "M1", settings, dry_run=False)

    data = json.loads(Path(status_path).read_text())
    assert data["outcome"] == "auto_disabled"
    assert data["signal_reason"] == "range_fade_vah"


@pytest.mark.asyncio
async def test_execute_trade_writes_order_placed_status(tmp_path, tmp_journal, risk_manager):
    status_path = _status_path(tmp_path)
    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = [
        {"symbolName": "US500", "pips": 100.0, "filledVolume": 1.0, "grossProfit": 100.0},
    ]
    mcp.place_market_order.return_value = {"positionId": 1234}
    mcp.get_positions.return_value = []

    with patch("ctrader_bot.execution.live_runner.LAST_CYCLE_STATUS_PATH", status_path), \
         patch("ctrader_bot.execution.live_runner.asyncio.sleep", new=AsyncMock()):
        await _execute_trade(
            mcp, tmp_journal, risk_manager, "US500", Side.BUY,
            entry_price=100.0, raw_stop_price=95.0, target_price=105.0,
            reason="test_status", regime="RANGE", atr_val=1.0, settings=_settings(),
            symbol_details=_symbol_details(), dry_run=False, registry=None,
        )

    data = json.loads(Path(status_path).read_text())
    assert data["outcome"] == "order_placed"
    assert data["position_id"] == "1234"


@pytest.mark.asyncio
async def test_execute_trade_writes_dry_run_status(tmp_path, tmp_journal, risk_manager):
    status_path = _status_path(tmp_path)
    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = [
        {"symbolName": "US500", "pips": 100.0, "filledVolume": 1.0, "grossProfit": 100.0},
    ]

    with patch("ctrader_bot.execution.live_runner.LAST_CYCLE_STATUS_PATH", status_path):
        await _execute_trade(
            mcp, tmp_journal, risk_manager, "US500", Side.BUY,
            entry_price=100.0, raw_stop_price=95.0, target_price=105.0,
            reason="test_dry_status", regime="RANGE", atr_val=1.0, settings=_settings(),
            symbol_details=_symbol_details(), dry_run=True, registry=None,
        )

    data = json.loads(Path(status_path).read_text())
    assert data["outcome"] == "dry_run"
    mcp.place_market_order.assert_not_called()


@pytest.mark.asyncio
async def test_execute_trade_writes_no_vpp_status(tmp_path, tmp_journal, risk_manager):
    status_path = _status_path(tmp_path)
    mcp = AsyncMock()
    mcp.get_balance.return_value = {"equity": 10000.0, "balance": 10000.0}
    mcp.get_deals.return_value = []  # no history, no fallback configured

    with patch("ctrader_bot.execution.live_runner.LAST_CYCLE_STATUS_PATH", status_path):
        await _execute_trade(
            mcp, tmp_journal, risk_manager, "US500", Side.BUY,
            entry_price=100.0, raw_stop_price=95.0, target_price=105.0,
            reason="test_no_vpp_status", regime="RANGE", atr_val=1.0, settings=_settings(),
            symbol_details=_symbol_details(), dry_run=False, registry=None,
        )

    data = json.loads(Path(status_path).read_text())
    assert data["outcome"] == "no_vpp"
