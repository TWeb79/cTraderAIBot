import pandas as pd

from ctrader_bot.indicators.volume_profile import build_volume_profile, session_key


def test_single_price_bin_all_volume():
    bars = pd.DataFrame({
        "low": [100.0, 100.0, 100.0],
        "high": [100.0, 100.0, 100.0],
        "volume": [10, 20, 30],
    })
    profile = build_volume_profile(bars, bin_size=1.0)
    assert profile.total_volume == 60
    assert profile.poc == 100.5  # bin midpoint for [100, 101)
    assert profile.val <= profile.poc <= profile.vah


def test_poc_is_highest_volume_bin():
    bars = pd.DataFrame({
        "low": [100.0, 105.0, 110.0],
        "high": [101.0, 106.0, 111.0],
        "volume": [10, 1000, 10],
    })
    profile = build_volume_profile(bars, bin_size=1.0)
    assert 105.0 <= profile.poc <= 106.0


def test_value_area_covers_target_pct_of_volume():
    bars = pd.DataFrame({
        "low": list(range(100, 110)),
        "high": [x + 1 for x in range(100, 110)],
        "volume": [10] * 10,
    })
    profile = build_volume_profile(bars, bin_size=1.0, value_area_pct=0.70)
    in_value_area = profile.bins[(profile.bins.index >= profile.val) & (profile.bins.index <= profile.vah)].sum()
    assert in_value_area / profile.total_volume >= 0.70
    assert profile.val <= profile.poc <= profile.vah


def test_range_distributes_volume_across_spanned_bins():
    bars = pd.DataFrame({"low": [100.0], "high": [104.0], "volume": [40.0]})
    profile = build_volume_profile(bars, bin_size=1.0)
    assert abs(profile.total_volume - 40.0) < 1e-6
    assert len(profile.bins[profile.bins > 0]) == 4


def test_empty_bars_raises():
    import pytest
    with pytest.raises(ValueError):
        build_volume_profile(pd.DataFrame(columns=["low", "high", "volume"]), bin_size=1.0)


def test_session_key_rolls_over_before_utc_hour():
    ts_before = pd.Timestamp("2026-08-06T20:00:00Z")
    ts_after = pd.Timestamp("2026-08-06T22:00:00Z")
    assert session_key(ts_before, session_rollover_utc_hour=21) == pd.Timestamp("2026-08-05", tz="UTC")
    assert session_key(ts_after, session_rollover_utc_hour=21) == pd.Timestamp("2026-08-06", tz="UTC")
