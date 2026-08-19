"""Hard-enforced risk rules: position sizing, mandatory stop-loss, daily-loss
circuit breaker, max aggregate open risk. Used identically by the backtest
engine and the live runner.

Note on position sizing: US500 (and CFD indices generally) profit/loss per
index-point per lot depends on an account-currency/instrument-currency FX
conversion that no get_symbol_details field exposes directly. See
`estimate_value_per_point_per_lot` — it derives this empirically from recent
closed deals (get_deals) rather than assuming a hardcoded constant, since the
true value drifts with FX rates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade_pct: float
    max_daily_loss_pct: float
    max_open_risk_pct: float
    min_stop_atr_mult: float


def estimate_value_per_point_per_lot(deals: list[dict], symbol: str) -> float | None:
    """Empirically estimate account-currency value per price-point per lot
    from recent closed deals for `symbol`, using grossProfit / (pips * volume).
    Returns None if no usable deals are found — callers must not fall back to
    a guessed constant; refuse to size trades instead.
    """
    ratios = []
    for d in deals:
        if d.get("symbolName") != symbol:
            continue
        pips = d.get("pips")
        vol = d.get("filledVolume") or d.get("volume")
        gp = d.get("grossProfit")
        if not pips or not vol or gp is None:
            continue
        ratios.append(abs(gp) / (abs(pips) * vol))
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def compute_stop_distance(atr: float, raw_stop_distance: float, min_stop_atr_mult: float) -> float:
    """Enforces a stop-distance floor (in ATR) so stops aren't unrealistically tight."""
    return max(raw_stop_distance, min_stop_atr_mult * atr)


@dataclass
class RiskManager:
    limits: RiskLimits
    day_start_equity: float = 0.0
    current_session_date: object | None = None
    realized_pnl_today: float = 0.0
    halted_today: bool = False
    open_risk: dict[str, float] = field(default_factory=dict)

    def start_new_session(self, session_date: object, equity: float) -> None:
        self.current_session_date = session_date
        self.day_start_equity = equity
        self.realized_pnl_today = 0.0
        self.halted_today = False

    def record_realized_pnl(self, pnl: float) -> None:
        self.realized_pnl_today += pnl
        if self.day_start_equity > 0:
            loss_pct = -self.realized_pnl_today / self.day_start_equity * 100
            if loss_pct >= self.limits.max_daily_loss_pct:
                self.halted_today = True

    def can_open_new_trade(self) -> bool:
        return not self.halted_today

    def current_open_risk_amount(self) -> float:
        return sum(self.open_risk.values())

    def size_trade(
        self,
        equity: float,
        entry_price: float,
        stop_price: float,
        value_per_point_per_lot: float,
        min_volume: float,
        max_volume: float,
        volume_step: float,
    ) -> float | None:
        """Returns lot size, or None if no stop-loss, no risk budget left, or
        the resulting size would be below the symbol's minimum tradable volume.
        """
        if not self.can_open_new_trade():
            return None
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            return None  # no unprotected positions

        desired_risk = equity * self.limits.risk_per_trade_pct / 100
        max_open_risk_amount = equity * self.limits.max_open_risk_pct / 100
        available = max_open_risk_amount - self.current_open_risk_amount()
        risk_amount = min(desired_risk, max(available, 0.0))
        if risk_amount <= 0:
            return None

        volume_lots = risk_amount / (stop_distance * value_per_point_per_lot)
        steps = math.floor(volume_lots / volume_step)
        volume_lots = steps * volume_step
        volume_lots = min(volume_lots, max_volume)
        if volume_lots < min_volume:
            return None
        return round(volume_lots, 8)

    def register_open_trade(self, trade_id: str, risk_amount: float) -> None:
        self.open_risk[trade_id] = risk_amount

    def register_closed_trade(self, trade_id: str, realized_pnl: float) -> None:
        self.open_risk.pop(trade_id, None)
        self.record_realized_pnl(realized_pnl)
