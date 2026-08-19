"""Next-bar prediction for auto-mode.

Given a set of enriched bars (already run through
``backtest.engine.prepare_backtest_bars``) and a selected strategy, compute:

  - direction  LONG / SHORT / FLAT
  - entry / stop / target prices (the next-5min plan on the signal timeframe)
  - a likelihood (0..1) the direction is correct, derived from trained data

This is fully deterministic: it calls the same ``evaluate_bar`` the live loop
uses, then blends a confidence from the persisted parameter-registry
performance + live/simulated feedback for the active regime/setup. No model
inference, no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ctrader_bot.strategy.signals import Side, evaluate_bar
from ctrader_bot.strategy.strategies import get_strategy
from ctrader_bot.training.registry import ParameterRegistry


def _clamp(x: float, lo: float = 0.05, hi: float = 0.95) -> float:
    return max(lo, min(hi, x))


@dataclass
class Prediction:
    direction: str  # LONG | SHORT | FLAT
    entry: float | None
    stop: float | None
    target: float | None
    likelihood: float
    regime: str
    reason: str
    rr: float | None
    source: str  # signal | no-signal | disabled | untrained
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "likelihood": round(self.likelihood, 4),
            "regime": self.regime,
            "reason": self.reason,
            "rr": round(self.rr, 3) if self.rr is not None else None,
            "source": self.source,
            "note": self.note,
        }


def _estimate_likelihood(reason: str, regime: str, registry: ParameterRegistry | None,
                          use_trained: bool) -> tuple[float, str]:
    if not use_trained or registry is None:
        return 0.5, "untrained — neutral 0.5 prior (enable trained params to weight by history)"

    perf = registry.get_performance()
    base = float(perf.get("win_rate", 0.5)) if perf else 0.5

    feedback = registry.get_live_feedback_summary()
    regime_fb = feedback.get("by_regime", {}).get(regime)
    tag_fb = feedback.get("by_setup_tag", {}).get(reason)

    adj = 0.0
    parts = []
    if regime_fb and regime_fb.get("n", 0) > 0:
        r = max(-1.0, min(1.0, regime_fb["avg_r"]))
        adj += r * 0.20
        parts.append(f"regime avgR={r:+.2f}")
    if tag_fb and tag_fb.get("n", 0) > 0:
        r = max(-1.0, min(1.0, tag_fb["avg_r"]))
        adj += r * 0.15
        parts.append(f"setup avgR={r:+.2f}")

    likelihood = _clamp(base + adj)
    note = "trained: " + (", ".join(parts) if parts else f"base win_rate={base:.2f}")
    return likelihood, note


def predict_next(bars: pd.DataFrame, strategy_name: str | None = None,
                 use_trained: bool = False, registry_path: str | None = None) -> Prediction:
    """Predict the next signal on the latest enriched bar.

    ``bars`` must already contain regime / poc_prev / vah_prev / val_prev /
    close_prev (i.e. have been through ``prepare_backtest_bars``).
    """
    strategy = get_strategy(strategy_name)
    registry = ParameterRegistry(registry_path) if use_trained else None

    params = dict(strategy.params)
    if use_trained and registry is not None:
        trained = registry.load_best_params()
        for k in ("level_proximity_atr_mult", "breakout_confirm_atr_mult", "trend_direction_lookback"):
            if k in trained and trained[k] is not None:
                params[k] = trained[k]

    if bars is None or len(bars) == 0:
        return Prediction("FLAT", None, None, None, 0.5, "UNKNOWN", "no-data", None,
                           "no-data", "no bars available")

    latest = bars.iloc[-1]
    lookback = int(params.get("trend_direction_lookback", 20))
    recent_closes = bars["close"].iloc[max(0, len(bars) - lookback - 1):]
    atr = latest.get("atr")

    try:
        signal = evaluate_bar(
            latest, recent_closes, atr,
            level_proximity_atr_mult=params["level_proximity_atr_mult"],
            breakout_confirm_atr_mult=params["breakout_confirm_atr_mult"],
            trend_direction_lookback=lookback,
        )
    except Exception as e:  # defensive: a malformed bar should not crash the UI
        return Prediction("FLAT", None, None, None, 0.5, str(latest.get("regime", "UNKNOWN")),
                           "eval-error", None, "no-signal", f"evaluate_bar failed: {e}")

    if signal is None:
        return Prediction("FLAT", None, None, None, 0.5, str(latest.get("regime", "UNKNOWN")),
                           "no-signal", None, "no-signal", "no signal on latest bar")

    if not strategy.accepts(signal.reason):
        return Prediction("FLAT", None, None, None, 0.5, str(signal.regime),
                           signal.reason, None, "disabled",
                           f"strategy '{strategy.name}' does not enable {signal.reason}")

    direction = "LONG" if signal.side == Side.BUY else "SHORT"
    entry, sl, tp = signal.entry_price, signal.stop_price, signal.target_price
    rr = None
    if entry not in (None, 0) and sl not in (None, 0) and abs(entry - sl) > 0:
        rr = abs(tp - entry) / abs(entry - sl)

    likelihood, note = _estimate_likelihood(signal.reason, str(signal.regime), registry, use_trained)
    return Prediction(direction, entry, sl, tp, likelihood, str(signal.regime),
                       signal.reason, rr, "signal", note)
