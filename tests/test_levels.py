import pandas as pd

from ctrader_bot.strategy.levels import (
    attach_prior_session_levels,
    compute_ny_open_gap_state,
    compute_session_levels,
)


def _minute_bars(session_start_utc: str, n: int, base_price: float) -> pd.DataFrame:
    idx = pd.date_range(session_start_utc, periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({
        "timestamp": idx,
        "low": base_price - 0.5,
        "high": base_price + 0.5,
        "close": base_price,
        "volume": 10.0,
    })


def test_compute_session_levels_two_sessions():
    day1 = _minute_bars("2026-08-03T22:00:00Z", 60, 100.0)  # session_date 2026-08-03 (rollover 21:00 UTC)
    day2 = _minute_bars("2026-08-04T22:00:00Z", 60, 110.0)  # session_date 2026-08-04
    bars = pd.concat([day1, day2], ignore_index=True)

    levels = compute_session_levels(bars, bin_size=1.0)
    assert len(levels) == 2
    assert abs(levels.loc[pd.Timestamp("2026-08-03", tz="UTC"), "close"] - 100.0) < 1e-6
    assert abs(levels.loc[pd.Timestamp("2026-08-04", tz="UTC"), "close"] - 110.0) < 1e-6


def test_attach_prior_session_levels_no_lookahead():
    day1 = _minute_bars("2026-08-03T22:00:00Z", 60, 100.0)
    day2 = _minute_bars("2026-08-04T22:00:00Z", 60, 110.0)
    bars = pd.concat([day1, day2], ignore_index=True)
    levels = compute_session_levels(bars, bin_size=1.0)

    signal_bars = bars.iloc[::5].reset_index(drop=True)  # every 5th bar as our "signal timeframe"
    attached = attach_prior_session_levels(signal_bars, levels)

    day1_rows = attached[attached["session_date"] == pd.Timestamp("2026-08-03", tz="UTC")]
    day2_rows = attached[attached["session_date"] == pd.Timestamp("2026-08-04", tz="UTC")]

    assert day1_rows["close_prev"].isna().all()  # no session before day1 in this dataset
    assert (day2_rows["close_prev"] == 100.0).all()  # day2 sees day1's close, never its own


def test_ny_open_gap_state_detects_and_resolves_gap_above():
    idx = pd.date_range("2026-08-04T13:00:00Z", periods=90, freq="1min", tz="UTC")
    closes = [105.0] * 20 + list(pd.Series(range(20)).apply(lambda i: 105.0 - i * 0.3)) + [99.0] * 50
    bars = pd.DataFrame({
        "timestamp": idx,
        "close": closes[:90],
        "session_date": pd.Timestamp("2026-08-04", tz="UTC"),
        "close_prev": 100.0,
    })
    out = compute_ny_open_gap_state(bars, ny_open_utc="13:30", gap_fill_window_minutes=60)

    window_rows = out[out["in_gap_window"]]
    assert (window_rows["gap_direction"] == "above").all()
    assert out["touched_close_prev"].any()
    assert out.iloc[0]["touched_close_prev"] == False  # before window opens, not yet touched


def test_ny_open_gap_state_no_gap_when_no_prior_close():
    idx = pd.date_range("2026-08-04T13:00:00Z", periods=10, freq="1min", tz="UTC")
    bars = pd.DataFrame({
        "timestamp": idx,
        "close": 100.0,
        "session_date": pd.Timestamp("2026-08-04", tz="UTC"),
        "close_prev": float("nan"),
    })
    out = compute_ny_open_gap_state(bars, ny_open_utc="13:30", gap_fill_window_minutes=60)
    assert (out["gap_direction"] == "none").all()
    assert not out["touched_close_prev"].any()
