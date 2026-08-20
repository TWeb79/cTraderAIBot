"""Session-anchored VWAP — resets at the same daily rollover boundary used by
the volume profile (indicators.volume_profile.session_key), so "VWAP" means
the same trading session everywhere in this codebase. Used as a dynamic
support/resistance level for bounce signals (implementationplan.md §15.9),
not as a standalone trend filter.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ctrader_bot.indicators.volume_profile import session_key


def session_vwap(bars: pd.DataFrame, session_rollover_utc_hour: int = 21) -> pd.Series:
    """bars: DataFrame with columns timestamp, high, low, close, volume (any
    timeframe — typically the same timeframe the strategy runs on).

    Returns a Series aligned to `bars.index`: the cumulative
    volume-weighted typical price ((high+low+close)/3), reset to start
    accumulating fresh at each session's first bar. Volume here is whatever
    get_trendbars reports (tick-volume proxy for CFDs — see
    volume_profile.py's module docstring), so this is a liquidity-weighted
    average price, not a literal exchange VWAP.
    """
    if bars.empty:
        return pd.Series(dtype=float, index=bars.index)

    typical_price = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    volume = bars["volume"].clip(lower=0)
    tp_vol = typical_price * volume

    sessions = bars["timestamp"].apply(lambda ts: session_key(ts, session_rollover_utc_hour))
    cum_tp_vol = tp_vol.groupby(sessions).cumsum()
    cum_vol = volume.groupby(sessions).cumsum()

    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    return vwap.astype(float)
