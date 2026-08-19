"""Trading-session open/close windows for the dashboard chart.

All times are UTC. These are display/annotation windows only — they do not
change any strategy logic. The cTrader MCP feed is US/European index hours,
so the three liquid sessions the user cares about are:

  - Asia (Tokyo)       00:00–06:00 UTC
  - Frankfurt (Xetra)  07:00–15:30 UTC
  - New York (NYSE)    13:30–20:00 UTC  (09:30–16:00 ET, daylight time)

Overridable via ``config.yaml`` ``sessions_display`` if needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import Any


@dataclass(frozen=True)
class SessionWindow:
    key: str
    label: str
    open: time
    close: time

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "open": self.open.strftime("%H:%M"),
            "close": self.close.strftime("%H:%M"),
        }


DEFAULT_WINDOWS: list[SessionWindow] = [
    SessionWindow("asia", "Asia", time(0, 0), time(6, 0)),
    SessionWindow("frankfurt", "Frankfurt", time(7, 0), time(15, 30)),
    SessionWindow("ny", "New York", time(13, 30), time(20, 0)),
]


def session_windows() -> list[SessionWindow]:
    return list(DEFAULT_WINDOWS)


def session_markers(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Return vertical marker descriptors between ``start`` and ``end`` (UTC).

    Each marker: {ts: ISO, label, kind: 'open'|'close', session}.
    """
    if not DEFAULT_WINDOWS:
        return []
    lo = min(start, end).replace(hour=0, minute=0, second=0, microsecond=0)
    hi = max(start, end)
    markers: list[dict[str, Any]] = []
    day = lo
    while day <= hi:
        for w in DEFAULT_WINDOWS:
            for kind, tod in (("open", w.open), ("close", w.close)):
                ts = day.replace(hour=tod.hour, minute=tod.minute, second=0, microsecond=0)
                if start <= ts <= end:
                    markers.append({
                        "ts": ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "label": w.label,
                        "kind": kind,
                        "session": w.key,
                    })
        day = day + timedelta(days=1)
    return sorted(markers, key=lambda m: m["ts"])
