"""Named trading strategies exposed to the dashboard / auto-mode.

Each strategy is a deterministic parameterization of the single
``strategy.signals.evaluate_bar`` engine plus a set of enabled signal
"families" (so a strategy can opt in to only some setup types). This keeps
strategy selection 100% deterministic — there is no ML in the strategy path.

Signal family is derived from the ``Signal.reason`` prefix:
  - ny_open_gap_fill
  - range_fade_*      (range_fade_vah / range_fade_val)
  - breakout_*        (breakout_continuation_above_vah / _below_val)
  - trend_pullback_*  (trend_pullback_poc)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def family_of(reason: str) -> str:
    """Map a signal ``reason`` to its strategy family."""
    if reason.startswith("ny_open_gap_fill"):
        return "gap_fill"
    if reason.startswith("range_fade"):
        return "range_fade"
    if reason.startswith("breakout"):
        return "breakout"
    if reason.startswith("trend_pullback"):
        return "trend_pullback"
    return "other"


@dataclass(frozen=True)
class StrategyDef:
    name: str
    label: str
    description: str
    params: dict[str, float]
    enabled_families: frozenset[str] = field(default_factory=frozenset)
    default: bool = False

    def accepts(self, reason: str) -> bool:
        return family_of(reason) in self.enabled_families

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "params": dict(self.params),
            "enabled_families": sorted(self.enabled_families),
            "default": self.default,
        }


_STRATEGIES: dict[str, StrategyDef] = {
    "balanced": StrategyDef(
        name="balanced",
        label="Balanced (all setups)",
        description="Default blend: gap-fill, range fades, breakouts and trend pullbacks — the full evaluate_bar logic.",
        params={
            "level_proximity_atr_mult": 0.25,
            "breakout_confirm_atr_mult": 0.15,
            "trend_direction_lookback": 20,
        },
        enabled_families=frozenset({"gap_fill", "range_fade", "breakout", "trend_pullback"}),
        default=True,
    ),
    "volume_profile_fade": StrategyDef(
        name="volume_profile_fade",
        label="Volume-Profile Fade",
        description="Mean-reversion to prior-session VAH/VAL in range regimes only. Lowest frequency, highest selectivity.",
        params={
            "level_proximity_atr_mult": 0.20,
            "breakout_confirm_atr_mult": 0.15,
            "trend_direction_lookback": 20,
        },
        enabled_families=frozenset({"range_fade"}),
    ),
    "breakout_momentum": StrategyDef(
        name="breakout_momentum",
        label="Breakout Momentum",
        description="Trades breaks of prior-session VAH/VAL and trend pullbacks. Favours trending/breakout regimes.",
        params={
            "level_proximity_atr_mult": 0.25,
            "breakout_confirm_atr_mult": 0.10,
            "trend_direction_lookback": 15,
        },
        enabled_families=frozenset({"breakout", "trend_pullback"}),
    ),
    "trend_pullback": StrategyDef(
        name="trend_pullback",
        label="Trend Pullback",
        description="Only pulls back to the prior POC inside an established trend. Longer trend lookback for stability.",
        params={
            "level_proximity_atr_mult": 0.30,
            "breakout_confirm_atr_mult": 0.15,
            "trend_direction_lookback": 30,
        },
        enabled_families=frozenset({"trend_pullback", "breakout"}),
    ),
    "ny_gap_fill": StrategyDef(
        name="ny_gap_fill",
        label="NY Open Gap Fill",
        description="Single-setup strategy: fades the NY-open gap toward the prior close while the window is active.",
        params={
            "level_proximity_atr_mult": 0.25,
            "breakout_confirm_atr_mult": 0.15,
            "trend_direction_lookback": 20,
        },
        enabled_families=frozenset({"gap_fill"}),
    ),
}


def list_strategies() -> list[dict[str, Any]]:
    return [s.to_dict() for s in _STRATEGIES.values()]


def get_strategy(name: str | None) -> StrategyDef:
    if not name:
        return _STRATEGIES["balanced"]
    return _STRATEGIES.get(name, _STRATEGIES["balanced"])


def default_strategy_name() -> str:
    for s in _STRATEGIES.values():
        if s.default:
            return s.name
    return next(iter(_STRATEGIES))
