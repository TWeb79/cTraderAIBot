"""Tests for trading-session window markers (Asia / Frankfurt / NY)."""

from __future__ import annotations

from datetime import datetime, timezone

from ctrader_bot.strategy.sessions import session_markers, session_windows


def test_session_windows_present():
    windows = session_windows()
    keys = {w.key for w in windows}
    assert {"asia", "frankfurt", "ny"}.issubset(keys)
    for w in windows:
        assert w.open < w.close


def test_session_markers_cover_open_and_close():
    start = datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 19, 23, 59, tzinfo=timezone.utc)
    markers = session_markers(start, end)
    labels = {(m["session"], m["kind"]) for m in markers}
    assert ("asia", "open") in labels
    assert ("asia", "close") in labels
    assert ("frankfurt", "open") in labels
    assert ("ny", "close") in labels
    # NY open is 13:30 UTC -> within the day range
    ny_open = [m for m in markers if m["session"] == "ny" and m["kind"] == "open"][0]
    assert ny_open["ts"].endswith("13:30:00Z")


def test_session_markers_respect_range():
    start = datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 19, 15, 0, tzinfo=timezone.utc)
    markers = session_markers(start, end)
    # Only markers whose timestamp falls in [start, end] are returned.
    for m in markers:
        ts = datetime.fromisoformat(m["ts"].replace("Z", "+00:00"))
        assert start <= ts <= end
