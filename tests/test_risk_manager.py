import pytest

from ctrader_bot.risk.risk_manager import (
    RiskLimits,
    RiskManager,
    compute_stop_distance,
    estimate_value_per_point_per_lot,
)


def _limits(**overrides):
    base = dict(risk_per_trade_pct=1.5, max_daily_loss_pct=5.0, max_open_risk_pct=10.0, min_stop_atr_mult=0.5)
    base.update(overrides)
    return RiskLimits(**base)


def test_estimate_value_per_point_per_lot_from_deals():
    deals = [
        {"symbolName": "US500", "pips": 0.2, "filledVolume": 4.6, "grossProfit": 0.8},
        {"symbolName": "US500", "pips": 1.6, "filledVolume": 4.6, "grossProfit": 6.38},
        {"symbolName": "EURUSD", "pips": 10, "filledVolume": 1, "grossProfit": 100},  # different symbol, ignored
    ]
    value = estimate_value_per_point_per_lot(deals, "US500")
    assert value is not None
    assert 0.8 < value < 0.95


def test_estimate_value_per_point_per_lot_no_data_returns_none():
    assert estimate_value_per_point_per_lot([], "US500") is None
    assert estimate_value_per_point_per_lot([{"symbolName": "EURUSD", "pips": 1, "filledVolume": 1, "grossProfit": 1}], "US500") is None


def test_compute_stop_distance_enforces_floor():
    assert compute_stop_distance(atr=4.0, raw_stop_distance=1.0, min_stop_atr_mult=0.5) == 2.0
    assert compute_stop_distance(atr=4.0, raw_stop_distance=5.0, min_stop_atr_mult=0.5) == 5.0


def test_size_trade_basic_risk_pct():
    rm = RiskManager(limits=_limits())
    # equity 10000, risk 1.5% = 150; stop_distance=10; value_per_point_per_lot=0.87
    # volume = 150 / (10*0.87) = 17.24 lots, but capped by max_open_risk (10% = 1000, fine here)
    vol = rm.size_trade(equity=10000, entry_price=100, stop_price=90,
                         value_per_point_per_lot=0.87, min_volume=0.01, max_volume=100, volume_step=0.01)
    assert vol is not None
    expected = 150 / (10 * 0.87)
    assert abs(vol - round(expected, 2)) < 0.02


def test_size_trade_returns_none_without_stop():
    rm = RiskManager(limits=_limits())
    vol = rm.size_trade(equity=10000, entry_price=100, stop_price=100,
                         value_per_point_per_lot=0.87, min_volume=0.01, max_volume=100, volume_step=0.01)
    assert vol is None


def test_size_trade_below_min_volume_returns_none():
    rm = RiskManager(limits=_limits(risk_per_trade_pct=0.001))
    vol = rm.size_trade(equity=100, entry_price=100, stop_price=50,
                         value_per_point_per_lot=0.87, min_volume=0.01, max_volume=100, volume_step=0.01)
    assert vol is None


def test_daily_loss_circuit_breaker_halts_new_trades():
    rm = RiskManager(limits=_limits(max_daily_loss_pct=5.0))
    rm.start_new_session("2026-08-04", equity=10000)
    assert rm.can_open_new_trade()
    rm.record_realized_pnl(-600)  # 6% loss > 5% limit
    assert not rm.can_open_new_trade()
    vol = rm.size_trade(equity=9400, entry_price=100, stop_price=90,
                         value_per_point_per_lot=0.87, min_volume=0.01, max_volume=100, volume_step=0.01)
    assert vol is None


def test_daily_loss_resets_on_new_session():
    rm = RiskManager(limits=_limits())
    rm.start_new_session("2026-08-04", equity=10000)
    rm.record_realized_pnl(-600)
    assert not rm.can_open_new_trade()
    rm.start_new_session("2026-08-05", equity=9400)
    assert rm.can_open_new_trade()


def test_max_open_risk_limits_additional_sizing():
    rm = RiskManager(limits=_limits(risk_per_trade_pct=5.0, max_open_risk_pct=10.0))
    rm.register_open_trade("t1", risk_amount=900)  # 9% of 10000 already committed
    vol = rm.size_trade(equity=10000, entry_price=100, stop_price=90,
                         value_per_point_per_lot=0.87, min_volume=0.01, max_volume=100, volume_step=0.01)
    # only 1% (100) of risk budget left, not the full 5% requested
    assert vol is not None
    expected_max = 100 / (10 * 0.87)
    assert vol <= round(expected_max, 2) + 0.01


def test_register_closed_trade_frees_open_risk_and_records_pnl():
    rm = RiskManager(limits=_limits())
    rm.start_new_session("2026-08-04", equity=10000)
    rm.register_open_trade("t1", risk_amount=150)
    assert rm.current_open_risk_amount() == 150
    rm.register_closed_trade("t1", realized_pnl=-50)
    assert rm.current_open_risk_amount() == 0
    assert rm.realized_pnl_today == -50
