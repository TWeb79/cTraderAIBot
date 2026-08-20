"""Tests for the orderflow-footprint bucketing logic behind
GET /api/bars/{timestamp}/footprint and GET /api/bars/footprint
(implementationplan.md §15.2 + its "show footprint instead of a candle in
the Orderflow chart view" follow-up).

Only the pure `_footprint_from_sub_bars`/`_footprints_by_candle` helpers are
tested here — the FastAPI routes themselves need a live MCP connection and
aren't covered by this project's test suite (no other dashboard_api.py route
has a test either; see tests/ — this is the first, narrowly scoped to the
new deterministic logic).
"""

from datetime import datetime, timedelta, timezone

from api.dashboard_api import _footprint_from_sub_bars, _footprints_by_candle
from ctrader_bot.mcp_client import Bar


def _bar(open_, close_, volume=10.0, high=None, low=None, timestamp=None):
    return Bar(
        timestamp=timestamp or datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=open_, high=high if high is not None else max(open_, close_) + 0.1,
        low=low if low is not None else min(open_, close_) - 0.1,
        close=close_, volume=volume,
    )


def test_footprint_classifies_up_bar_as_buy_volume():
    bars = [_bar(100.0, 100.5, volume=20.0)]
    result = _footprint_from_sub_bars(bars, pip_size=1.0, bin_ticks=1)
    assert len(result["levels"]) == 1
    assert result["levels"][0]["buy_volume"] == 20.0
    assert result["levels"][0]["sell_volume"] == 0.0


def test_footprint_classifies_down_bar_as_sell_volume():
    bars = [_bar(100.5, 100.0, volume=15.0)]
    result = _footprint_from_sub_bars(bars, pip_size=1.0, bin_ticks=1)
    assert result["levels"][0]["sell_volume"] == 15.0
    assert result["levels"][0]["buy_volume"] == 0.0


def test_footprint_aggregates_multiple_bars_into_same_price_bucket():
    bars = [_bar(100.0, 100.4, volume=10.0), _bar(100.5, 100.1, volume=5.0)]
    result = _footprint_from_sub_bars(bars, pip_size=1.0, bin_ticks=10)
    assert len(result["levels"]) == 1
    assert result["levels"][0]["buy_volume"] == 10.0
    assert result["levels"][0]["sell_volume"] == 5.0
    assert result["levels"][0]["delta"] == 5.0


def test_footprint_identifies_high_demand_price():
    bars = [
        _bar(100.0, 100.1, volume=5.0, high=100.2, low=99.9),      # ~level 100
        _bar(110.0, 110.6, volume=50.0, high=110.7, low=109.9),    # ~level 110/111 -> highest volume
        _bar(120.0, 119.9, volume=1.0, high=120.1, low=119.8),     # ~level 120
    ]
    result = _footprint_from_sub_bars(bars, pip_size=1.0, bin_ticks=1)
    assert result["high_demand_price"] is not None
    # the 50-volume bar's bucket should dominate
    top = max(result["levels"], key=lambda l: l["buy_volume"] + l["sell_volume"])
    assert result["high_demand_price"] == top["price"]


def test_footprint_totals_sum_correctly():
    bars = [_bar(100.0, 100.4, volume=10.0), _bar(101.0, 100.6, volume=7.0)]
    result = _footprint_from_sub_bars(bars, pip_size=0.01, bin_ticks=5)
    assert result["total_buy_volume"] + result["total_sell_volume"] == 17.0


def test_footprint_empty_bars_returns_empty_levels():
    result = _footprint_from_sub_bars([], pip_size=1.0, bin_ticks=5)
    assert result["levels"] == []
    assert result["high_demand_price"] is None
    assert result["total_buy_volume"] == 0.0


# ── Bulk per-candle footprints (chart Orderflow view) ───────────────────

def _signal_bar(ts, close=100.0):
    return _bar(close - 0.1, close, volume=1.0, timestamp=ts)


def test_footprints_by_candle_groups_sub_bars_into_their_own_candle():
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    signal_bars = [_signal_bar(t0), _signal_bar(t0 + timedelta(minutes=5))]
    profile_bars = [
        _bar(100.0, 100.5, volume=10.0, timestamp=t0),                      # in candle 1
        _bar(100.0, 100.5, volume=3.0, timestamp=t0 + timedelta(minutes=4)),  # in candle 1
        _bar(101.0, 101.5, volume=7.0, timestamp=t0 + timedelta(minutes=5)),  # in candle 2
    ]
    result = _footprints_by_candle(signal_bars, profile_bars, "M5", pip_size=1.0, bin_ticks=1)

    assert set(result.keys()) == {t0.isoformat(), (t0 + timedelta(minutes=5)).isoformat()}
    candle1 = result[t0.isoformat()]
    assert candle1["total_buy_volume"] == 13.0
    candle2 = result[(t0 + timedelta(minutes=5)).isoformat()]
    assert candle2["total_buy_volume"] == 7.0


def test_footprints_by_candle_omits_candles_with_no_sub_bars():
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    signal_bars = [_signal_bar(t0), _signal_bar(t0 + timedelta(minutes=5))]
    profile_bars = [_bar(100.0, 100.5, volume=10.0, timestamp=t0)]  # only covers candle 1

    result = _footprints_by_candle(signal_bars, profile_bars, "M5", pip_size=1.0, bin_ticks=1)

    assert t0.isoformat() in result
    assert (t0 + timedelta(minutes=5)).isoformat() not in result


def test_footprints_by_candle_respects_candle_boundary_not_inclusive_of_next():
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    signal_bars = [_signal_bar(t0), _signal_bar(t0 + timedelta(minutes=5))]
    # A sub-bar exactly at the next candle's open belongs to candle 2, not candle 1.
    profile_bars = [_bar(100.0, 100.5, volume=10.0, timestamp=t0 + timedelta(minutes=5))]

    result = _footprints_by_candle(signal_bars, profile_bars, "M5", pip_size=1.0, bin_ticks=1)

    assert t0.isoformat() not in result
    assert (t0 + timedelta(minutes=5)).isoformat() in result


def test_footprints_by_candle_empty_inputs_returns_empty_dict():
    assert _footprints_by_candle([], [], "M5", pip_size=1.0, bin_ticks=1) == {}
