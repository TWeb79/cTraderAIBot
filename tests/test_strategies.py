"""Tests for the named strategy registry."""

from __future__ import annotations

from ctrader_bot.strategy.strategies import (
    family_of,
    get_strategy,
    list_strategies,
    default_strategy_name,
)


def test_list_strategies_includes_default():
    strategies = list_strategies()
    assert len(strategies) >= 3
    names = {s["name"] for s in strategies}
    assert "balanced" in names
    assert default_strategy_name() == "balanced"


def test_unknown_strategy_falls_back_to_balanced():
    assert get_strategy("does_not_exist").name == "balanced"
    assert get_strategy(None).default is True


def test_family_of_reasons():
    assert family_of("range_fade_vah") == "range_fade"
    assert family_of("breakout_continuation_above_vah") == "breakout"
    assert family_of("trend_pullback_poc") == "trend_pullback"
    assert family_of("ny_open_gap_fill") == "gap_fill"


def test_strategy_accepts_filters_families():
    fade = get_strategy("volume_profile_fade")
    assert fade.accepts("range_fade_val") is True
    assert fade.accepts("breakout_continuation_above_vah") is False

    gap = get_strategy("ny_gap_fill")
    assert gap.accepts("ny_open_gap_fill") is True
    assert gap.accepts("range_fade_vah") is False

    balanced = get_strategy("balanced")
    assert balanced.accepts("range_fade_vah") is True
    assert balanced.accepts("breakout_continuation_above_vah") is True
