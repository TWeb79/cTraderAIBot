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


def fixed_rr_target_price(entry_price: float, stop_price: float, target_rr_ratio: float) -> float:
    """Take-profit price at a fixed reward:risk ratio from the stop distance
    (implementationplan.md §15.7 — default 3:1). Direction-agnostic: mirrors
    the stop's signed offset from entry, so it works for both a long (stop
    below entry) and a short (stop above entry) without a Side parameter.
    """
    stop_offset = entry_price - stop_price
    return entry_price + stop_offset * target_rr_ratio


def margin_based_volume(
    free_margin: float | None,
    margin_pct: float,
    margin_per_lot: float | None,
    min_volume: float,
    max_volume: float,
    volume_step: float,
) -> float | None:
    """Position size from a target % of free margin (implementationplan.md
    §15.6), as an alternative/cap to risk_per_trade_pct-based sizing. Returns
    None if free margin or the per-lot margin cost isn't known, or if the
    resulting size would be below the symbol's minimum tradable volume —
    callers must not fall back to a guessed size (same policy as
    estimate_value_per_point_per_lot).
    """
    if not free_margin or free_margin <= 0:
        return None
    if not margin_per_lot or margin_per_lot <= 0:
        return None
    target_margin = free_margin * margin_pct / 100
    volume = target_margin / margin_per_lot
    steps = math.floor(volume / volume_step)
    volume = steps * volume_step
    volume = min(volume, max_volume)
    if volume < min_volume:
        return None
    return round(volume, 8)


def stop_improves(is_buy: bool, old_stop: float, candidate_stop: float) -> bool:
    """True if candidate_stop is strictly more favorable than old_stop —
    higher for a long, lower for a short. Enforces the "never move the stop
    backward" invariant from implementationplan.md §15.8.1.
    """
    return candidate_stop > old_stop if is_buy else candidate_stop < old_stop


def trailing_stop_update(
    is_buy: bool,
    entry_price: float,
    current_price: float,
    current_stop: float,
    current_target: float | None,
    pip_size: float,
    trigger_pips: float,
    lock_pips: float,
    tp_extend_trigger_pips: float,
    tp_extend_pips: float,
    sl_trail_distance_pips: float,
) -> tuple[float, float | None]:
    """Pure trailing-stop / TP-extension math (implementationplan.md §15.8 /
    §15.8.1). Returns (new_stop, new_target); a candidate stop is only ever
    adopted if `stop_improves` says it's strictly better than current_stop,
    so the stop never moves backward regardless of how price moves.

    Two independent, additive mechanisms:
      1. Profit-lock: once price is `trigger_pips` in profit, ratchet the
         stop to `lock_pips` beyond entry (in the trade's favor).
      2. TP-extend: once price is within `tp_extend_trigger_pips` of the
         current target, push the target out by `tp_extend_pips` and ratchet
         the stop to `sl_trail_distance_pips` behind the current price.
    """
    direction = 1 if is_buy else -1
    profit_pips = (current_price - entry_price) * direction / pip_size
    epsilon_pips = 1e-9  # float-noise guard so e.g. 2.9999999999999982 still counts as >= 3.0

    new_stop = current_stop
    new_target = current_target

    if profit_pips >= trigger_pips - epsilon_pips:
        candidate = entry_price + direction * lock_pips * pip_size
        if stop_improves(is_buy, new_stop, candidate):
            new_stop = candidate

    if current_target:
        distance_to_target_pips = abs(current_target - current_price) / pip_size
        if distance_to_target_pips <= tp_extend_trigger_pips + epsilon_pips:
            new_target = current_target + direction * tp_extend_pips * pip_size
            candidate = current_price - direction * sl_trail_distance_pips * pip_size
            if stop_improves(is_buy, new_stop, candidate):
                new_stop = candidate

    return new_stop, new_target


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
