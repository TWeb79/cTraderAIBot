"""Session volume-profile builder: POC (point of control) and value area (VAH/VAL).

Built from OHLCV bars using a standard range-distribution method: each bar's
volume is spread uniformly across the price bins its [low, high] range
overlaps (we don't have tick-level data within a bar, so uniform-within-range
is the standard approximation). Volume here is whatever get_trendbars
reports for the symbol — for a retail CFD/index feed like US500 that is tick
volume (count of price changes), not exchange-traded volume; treat the
resulting profile as a liquidity/activity proxy, not literal traded volume.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VolumeProfile:
    poc: float
    vah: float
    val: float
    bins: pd.Series  # index: bin midpoint price, values: volume in that bin
    total_volume: float


def build_volume_profile(bars: pd.DataFrame, bin_size: float, value_area_pct: float = 0.70) -> VolumeProfile:
    """bars must have columns: low, high, volume (one row per bar), covering a single session.

    bin_size: price width of each histogram bin (e.g. symbol pip size * price_bin_ticks).
    """
    if bars.empty:
        raise ValueError("build_volume_profile: bars is empty")
    if bin_size <= 0:
        raise ValueError("bin_size must be positive")

    session_low = bars["low"].min()
    session_high = bars["high"].max()
    n_bins = max(1, math.ceil((session_high - session_low) / bin_size) + 1)
    edges = session_low + bin_size * np.arange(n_bins + 1)
    volumes = np.zeros(n_bins)

    for low, high, vol in bars[["low", "high", "volume"]].itertuples(index=False):
        if vol <= 0:
            continue
        if high <= low:
            idx = min(int((low - session_low) // bin_size), n_bins - 1)
            volumes[idx] += vol
            continue
        bar_range = high - low
        first_bin = int((low - session_low) // bin_size)
        last_bin = min(int((high - session_low) // bin_size), n_bins - 1)
        for b in range(first_bin, last_bin + 1):
            bin_low = edges[b]
            bin_high = edges[b + 1]
            overlap = min(high, bin_high) - max(low, bin_low)
            if overlap > 0:
                volumes[b] += vol * (overlap / bar_range)

    midpoints = (edges[:-1] + edges[1:]) / 2
    bins = pd.Series(volumes, index=midpoints)

    total_volume = float(volumes.sum())
    poc_idx = int(np.argmax(volumes))
    poc = float(midpoints[poc_idx])

    if total_volume <= 0:
        return VolumeProfile(poc=poc, vah=poc, val=poc, bins=bins, total_volume=0.0)

    target = value_area_pct * total_volume
    lo = hi = poc_idx
    accumulated = volumes[poc_idx]
    while accumulated < target and (lo > 0 or hi < n_bins - 1):
        vol_below = volumes[lo - 1] if lo > 0 else -1.0
        vol_above = volumes[hi + 1] if hi < n_bins - 1 else -1.0
        if vol_above >= vol_below:
            hi += 1
            accumulated += volumes[hi]
        else:
            lo -= 1
            accumulated += volumes[lo]

    val = float(edges[lo])
    vah = float(edges[hi + 1])
    return VolumeProfile(poc=poc, vah=vah, val=val, bins=bins, total_volume=total_volume)


def session_key(timestamp: pd.Timestamp, session_rollover_utc_hour: int = 21) -> pd.Timestamp:
    """Maps a UTC bar timestamp to its trading-session date, given the daily
    rollover hour observed from get_trendbars d1 bars (~21:00 UTC for US500,
    matching the EST index-futures daily close/reopen). Bars before the
    rollover hour belong to the previous calendar day's session.
    """
    shifted = timestamp - pd.Timedelta(hours=session_rollover_utc_hour)
    return shifted.normalize()
