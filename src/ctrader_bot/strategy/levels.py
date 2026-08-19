"""Tracks prior-session POC/VAH/VAL/close as the current session's reference
("guidance") levels, and NY-open gap-fill state relative to the prior close.

Per the user's described edge: prior day's close is expected to be reached
once around NY session open (a "gap fill"), and prior day's POC acts as a
magnet more generally throughout the session.
"""

from __future__ import annotations

from datetime import time as _time

import numpy as np
import pandas as pd

from ctrader_bot.indicators.volume_profile import build_volume_profile, session_key


def _parse_hhmm(hhmm: str) -> _time:
    h, m = (int(x) for x in hhmm.split(":"))
    return _time(hour=h % 24, minute=m)


def _sub_session_label(ts: pd.Timestamp, ny_open_time: _time, rollover_time: _time) -> str:
    """Classify a bar's time-of-day as the 'ny' sub-session (NY open through the
    next rollover) or 'pre_ny' (everything else in that trading session: Asia +
    Frankfurt hours, wrapping across midnight up to NY open). Both boundaries
    are plain clock times, so this only needs the bar's own time-of-day — the
    session (calendar-day) grouping is handled separately via ``session_key``.
    """
    t = ts.time()
    if ny_open_time <= rollover_time:
        return "ny" if ny_open_time <= t < rollover_time else "pre_ny"
    # Unusual config where rollover happens before NY open on the 24h clock.
    return "pre_ny" if rollover_time <= t < ny_open_time else "ny"


def compute_session_levels(
    profile_bars: pd.DataFrame,
    bin_size: float,
    value_area_pct: float = 0.70,
    session_rollover_utc_hour: int = 21,
    ny_open_utc: str | None = None,
) -> pd.DataFrame:
    """profile_bars: fine-grained (e.g. M1) bars with columns timestamp, low, high, close, volume
    (an ``open`` column is used for ``ny_open_price`` when present, otherwise the
    NY window's first bar close is used as a proxy).

    Returns a DataFrame indexed by session_date with columns:
      poc, vah, val, close          — whole-session volume profile + session close
                                       (close = the session's own last traded price,
                                       used as *next* session's close_prev)
      day_close_price                — alias of ``close``, named for training-datapoint clarity
      poc_pre_ny, vah_pre_ny, val_pre_ny — volume profile over the pre-NY portion
                                       of the session (Asia + Frankfurt hours)
      poc_ny, vah_ny, val_ny         — volume profile over the NY-session portion
      ny_open_price                  — price at the first bar of the NY window

    The pre-NY/NY split columns are NaN for a session if ``ny_open_utc`` is not
    given, or if that sub-session has no bars in ``profile_bars`` (e.g. NY window
    not yet reached for the current, still-forming session).
    """
    df = profile_bars.copy()
    df["session_date"] = df["timestamp"].apply(lambda ts: session_key(ts, session_rollover_utc_hour))

    ny_open_time = _parse_hhmm(ny_open_utc) if ny_open_utc else None
    rollover_time = _time(hour=session_rollover_utc_hour % 24, minute=0)

    rows = []
    for sdate, group in df.groupby("session_date"):
        profile = build_volume_profile(group[["low", "high", "volume"]], bin_size=bin_size, value_area_pct=value_area_pct)
        row: dict = {
            "session_date": sdate,
            "poc": profile.poc,
            "vah": profile.vah,
            "val": profile.val,
            "close": group.iloc[-1]["close"],
            "day_close_price": group.iloc[-1]["close"],
            "poc_pre_ny": np.nan, "vah_pre_ny": np.nan, "val_pre_ny": np.nan,
            "poc_ny": np.nan, "vah_ny": np.nan, "val_ny": np.nan,
            "ny_open_price": np.nan,
        }

        if ny_open_time is not None:
            sub = group["timestamp"].apply(lambda ts: _sub_session_label(ts, ny_open_time, rollover_time))
            pre_ny_group = group[sub.values == "pre_ny"]
            ny_group = group[sub.values == "ny"]

            if not pre_ny_group.empty:
                p = build_volume_profile(pre_ny_group[["low", "high", "volume"]], bin_size=bin_size, value_area_pct=value_area_pct)
                row.update(poc_pre_ny=p.poc, vah_pre_ny=p.vah, val_pre_ny=p.val)

            if not ny_group.empty:
                p2 = build_volume_profile(ny_group[["low", "high", "volume"]], bin_size=bin_size, value_area_pct=value_area_pct)
                first_ny = ny_group.iloc[0]
                ny_open_price = float(first_ny["open"]) if "open" in ny_group.columns else float(first_ny["close"])
                row.update(poc_ny=p2.poc, vah_ny=p2.vah, val_ny=p2.val, ny_open_price=ny_open_price)

        rows.append(row)
    return pd.DataFrame(rows).set_index("session_date").sort_index()


def attach_prior_session_levels(
    signal_bars: pd.DataFrame,
    session_levels: pd.DataFrame,
    session_rollover_utc_hour: int = 21,
) -> pd.DataFrame:
    """signal_bars: bars (any timeframe) with a 'timestamp' column, the timeframe the
    strategy runs on. Adds columns poc_prev, vah_prev, val_prev, close_prev,
    each holding the *previous* session's levels (never today's — no lookahead).
    """
    out = signal_bars.copy()
    out["session_date"] = out["timestamp"].apply(lambda ts: session_key(ts, session_rollover_utc_hour))

    prior = session_levels.shift(1)
    prior.columns = [f"{c}_prev" for c in prior.columns]

    out = out.merge(prior, left_on="session_date", right_index=True, how="left")
    return out


def compute_ny_open_gap_state(
    bars: pd.DataFrame,
    ny_open_utc: str,
    gap_fill_window_minutes: int,
) -> pd.DataFrame:
    """bars must already have 'timestamp', 'close', 'session_date', 'close_prev'.

    Adds columns:
      in_gap_window: bool — within [ny_open, ny_open + window) for that session
      gap_direction: 'above' | 'below' | 'none' — price vs close_prev at the first
        bar of the gap window for that session
      touched_close_prev: bool — cumulative within the session: has price reached
        close_prev at or before this bar (only tracked once the gap window opens)
    """
    out = bars.copy()
    open_h, open_m = (int(x) for x in ny_open_utc.split(":"))
    window = pd.Timedelta(minutes=gap_fill_window_minutes)

    ny_open_time = pd.Timestamp("2000-01-01").replace(hour=open_h, minute=open_m).time()

    out["in_gap_window"] = out.groupby("session_date")["timestamp"].transform(
        lambda ts: (ts.dt.time >= ny_open_time) & (ts < (ts.dt.normalize() + pd.Timedelta(hours=open_h, minutes=open_m) + window))
    )

    gap_directions = []
    touched_flags = []
    for _sdate, group in out.groupby("session_date", sort=False):
        gap_dir = "none"
        touched = False
        window_rows = group[group["in_gap_window"]]
        if not window_rows.empty and not pd.isna(window_rows.iloc[0]["close_prev"]):
            first_close = window_rows.iloc[0]["close"]
            close_prev = window_rows.iloc[0]["close_prev"]
            if first_close > close_prev:
                gap_dir = "above"
            elif first_close < close_prev:
                gap_dir = "below"

        session_touched = []
        for _, row in group.iterrows():
            if row["in_gap_window"] and gap_dir != "none" and not pd.isna(row["close_prev"]):
                if gap_dir == "above" and row["close"] <= row["close_prev"]:
                    touched = True
                elif gap_dir == "below" and row["close"] >= row["close_prev"]:
                    touched = True
            session_touched.append(touched)

        gap_directions.extend([gap_dir] * len(group))
        touched_flags.extend(session_touched)

    out["gap_direction"] = gap_directions
    out["touched_close_prev"] = touched_flags
    return out
