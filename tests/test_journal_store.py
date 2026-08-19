import json
import os
import tempfile

import pytest

from ctrader_bot.journal.store import Journal, TradeDecision, TradeReflection


def _make_decision(symbol: str = "US500") -> TradeDecision:
    return TradeDecision(
        action="BUY",
        confidence=0.0,
        entry_type="market",
        entry_price=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        risk_reward_ratio=2.0,
        reasoning="test setup",
        invalidation_condition="stop hit",
    )


def _make_reflection() -> TradeReflection:
    return TradeReflection(
        outcome="WIN",
        r_multiple=1.5,
        what_matched_expectation="price moved to target",
        what_diverged="",
        lesson="worked as expected",
        setup_tag="trend_pullback_poc",
    )


def test_journal_creates_tables(tmp_path):
    db = str(tmp_path / "journal.sqlite3")
    j = Journal(db)
    assert os.path.exists(db)


def test_record_trade_and_get_trades(tmp_path):
    db = str(tmp_path / "journal.sqlite3")
    j = Journal(db)
    trade_id = j.record_trade(_make_decision(), _make_reflection(), "US500")
    assert trade_id == 1

    trades = j.get_trades(limit=10)
    assert len(trades) == 1
    assert trades[0]["symbol"] == "US500"
    assert trades[0]["r_multiple"] == 1.5
    assert trades[0]["setup_tag"] == "trend_pullback_poc"


def test_latest_digest_and_save_digest(tmp_path):
    db = str(tmp_path / "journal.sqlite3")
    j = Journal(db)

    assert j.latest_digest() == "No strategy digest yet — this is the first cycle."

    j.save_digest("first digest")
    assert j.latest_digest() == "first digest"

    j.save_digest("second digest")
    assert j.latest_digest() == "second digest"


def test_trades_since_last_digest(tmp_path):
    db = str(tmp_path / "journal.sqlite3")
    j = Journal(db)

    assert j.trades_since_last_digest() == 0

    j.record_trade(_make_decision(), _make_reflection(), "US500")
    j.record_trade(_make_decision(), _make_reflection(), "US500")
    assert j.trades_since_last_digest() == 2

    j.save_digest("digest 1")
    assert j.trades_since_last_digest() == 0

    j.record_trade(_make_decision(), _make_reflection(), "US500")
    assert j.trades_since_last_digest() == 1


def test_aggregate_stats(tmp_path):
    db = str(tmp_path / "journal.sqlite3")
    j = Journal(db)

    assert j.aggregate_stats() == {}

    j.record_trade(_make_decision(), _make_reflection(), "US500")
    j.record_trade(
        TradeDecision(
            action="SELL", confidence=0.0, entry_type="market",
            entry_price=100.0, stop_loss=101.0, take_profit=98.0,
            risk_reward_ratio=2.0, reasoning="test", invalidation_condition="stop",
        ),
        TradeReflection(outcome="LOSS", r_multiple=-1.0, what_matched_expectation="", what_diverged="", lesson="lost", setup_tag="range_fade_vah"),
        "US500",
    )

    stats = j.aggregate_stats()
    assert stats["n_trades"] == 2
    assert stats["win_rate"] == 0.5
    assert "by_tag" in stats
    assert "trend_pullback_poc" in stats["by_tag"]
    assert "range_fade_vah" in stats["by_tag"]


def test_save_and_load_cycle_state(tmp_path):
    db = str(tmp_path / "journal.sqlite3")
    j = Journal(db)

    assert j.load_cycle_state() is None

    j.save_cycle_state(["pos-1", "pos-2"])
    state = j.load_cycle_state()
    assert state is not None
    assert state["open_position_ids"] == ["pos-1", "pos-2"]
    assert "last_cycle_at" in state

    j.save_cycle_state(["pos-3"])
    state = j.load_cycle_state()
    assert state["open_position_ids"] == ["pos-3"]


def test_get_trades_returns_reflection_json(tmp_path):
    db = str(tmp_path / "journal.sqlite3")
    j = Journal(db)
    j.record_trade(_make_decision(), _make_reflection(), "US500")

    trades = j.get_trades()
    assert "reflection" in trades[0]
    assert trades[0]["reflection"]["outcome"] == "WIN"
    assert trades[0]["reflection"]["setup_tag"] == "trend_pullback_poc"
