"""Persistent parameter registry for deterministic "trained" settings.

This is the single source of truth for optimized parameter sets, performance
statistics, and live trade feedback. It persists to a JSON file
(default ``data/reports/parameter_registry.json``) so optimized parameters
survive process restarts.

Critical safety properties (see architecture.md §10):

* **Advisory only** — the live runner never reads ``best_params`` unless the
  operator explicitly passes ``--use-trained-params``. ``config.yaml`` remains
  the default source of truth.
* **No ML** — only persisted numeric parameter sets + aggregate stats.
* **Append-only feedback** — live trades are never deleted from the registry.
* **Deterministic** — loading params does not introduce randomness.

This module is never imported by the live loop except for the optional,
explicit ``--use-trained-params`` path and the write-only feedback append.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ctrader_bot.config import load_settings

REGISTRY_VERSION = "0.1.0"

# Parameter keys persisted and consumed by the live loop / optimizer.
PARAM_KEYS = (
    "level_proximity_atr_mult",
    "breakout_confirm_atr_mult",
    "trend_direction_lookback",
    "risk_per_trade_pct",
    "min_stop_atr_mult",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_path() -> Path:
    settings = load_settings()
    rel = settings.get("training", {}).get("registry_path", "data/reports/parameter_registry.json")
    return PROJECT_ROOT / rel


def _empty_feedback() -> dict[str, Any]:
    return {
        "n_live_trades": 0,
        "wins": 0,
        "live_win_rate": 0.0,
        "by_setup_tag": {},
        "by_regime": {},
    }


def _default_registry() -> dict[str, Any]:
    return {
        "version": REGISTRY_VERSION,
        "last_updated": None,
        "best_params": {},
        "best_params_by_regime": {},
        "performance": {},
        "live_feedback": _empty_feedback(),
        "optimization_history": [],
    }


def _update_bucket(bucket: dict[str, Any], r_multiple: float, pnl: float) -> dict[str, Any]:
    """Running mean update for a feedback bucket (setup tag / regime)."""
    n = bucket.get("n", 0) + 1
    prev_avg = bucket.get("avg_r", 0.0)
    bucket["n"] = n
    bucket["avg_r"] = (prev_avg * (n - 1) + r_multiple) / n
    bucket["pnl_sum"] = bucket.get("pnl_sum", 0.0) + pnl
    return bucket


class ParameterRegistry:
    """JSON-backed store for optimized params + live feedback."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _default_path()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                merged = _default_registry()
                merged.update(data)
                merged.setdefault("live_feedback", _empty_feedback())
                return merged
            except (json.JSONDecodeError, OSError):
                pass
        return _default_registry()

    def save(self) -> None:
        self._data["last_updated"] = _utcnow()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, default=str))

    # ── Best params ────────────────────────────────────────────────────────

    def save_best_params(self, params: dict[str, Any], metrics: dict[str, Any],
                         source: str = "backtest", regime: str | None = None) -> None:
        """Persist an optimized parameter set + performance metrics."""
        clean = {k: params.get(k) for k in PARAM_KEYS if k in params}
        entry = {
            "timestamp": _utcnow(),
            "source": source,
            "params": clean,
            "metrics": metrics,
        }
        if regime is None:
            self._data["best_params"] = clean
            self._data["performance"] = dict(metrics)
        else:
            self._data.setdefault("best_params_by_regime", {})[regime] = clean
        self._data.setdefault("optimization_history", []).append(entry)
        self.save()

    def load_best_params(self) -> dict[str, Any]:
        """Return the global best params (possibly empty dict)."""
        return dict(self._data.get("best_params", {}))

    def load_best_params_by_regime(self, regime: str) -> dict[str, Any]:
        """Return regime-specific params, falling back to global best."""
        by_regime = self._data.get("best_params_by_regime", {})
        return dict(by_regime.get(regime, self.load_best_params()))

    def get_performance(self) -> dict[str, Any]:
        return dict(self._data.get("performance", {}))

    def get_optimization_history(self, limit: int = 10) -> list[dict[str, Any]]:
        history = list(self._data.get("optimization_history", []))
        return history[-limit:][::-1]

    # ── Live feedback (append-only) ─────────────────────────────────────────

    def append_live_feedback(self, trade_record: dict[str, Any]) -> None:
        """Record a closed live trade outcome. Append-only, never deleted."""
        fb = self._data.setdefault("live_feedback", _empty_feedback())
        r_multiple = float(trade_record.get("r_multiple", 0.0))
        pnl = float(trade_record.get("pnl", 0.0))
        tag = trade_record.get("setup_tag", "unknown")
        regime = str(trade_record.get("regime", "UNKNOWN"))

        fb["n_live_trades"] = fb.get("n_live_trades", 0) + 1
        if r_multiple > 0:
            fb["wins"] = fb.get("wins", 0) + 1
        fb["live_win_rate"] = (fb["wins"] / fb["n_live_trades"]) if fb["n_live_trades"] else 0.0

        by_tag = fb.setdefault("by_setup_tag", {})
        by_tag[tag] = _update_bucket(by_tag.get(tag, {}), r_multiple, pnl)

        by_regime = fb.setdefault("by_regime", {})
        by_regime[regime] = _update_bucket(by_regime.get(regime, {}), r_multiple, pnl)

        self.save()

    def get_live_feedback_summary(self) -> dict[str, Any]:
        """Aggregated live stats (safe copy)."""
        return json.loads(json.dumps(self._data.get("live_feedback", _empty_feedback())))

    def export(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._data))


# ── Module-level convenience wrappers (default path) ───────────────────────

_DEFAULT_REGISTRY: ParameterRegistry | None = None


def _registry() -> ParameterRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ParameterRegistry()
    return _DEFAULT_REGISTRY


def save_best_params(params: dict[str, Any], metrics: dict[str, Any],
                     source: str = "backtest", regime: str | None = None) -> None:
    _registry().save_best_params(params, metrics, source=source, regime=regime)


def load_best_params() -> dict[str, Any]:
    return _registry().load_best_params()


def load_best_params_by_regime(regime: str) -> dict[str, Any]:
    return _registry().load_best_params_by_regime(regime)


def append_live_feedback(trade_record: dict[str, Any]) -> None:
    _registry().append_live_feedback(trade_record)


def get_live_feedback_summary() -> dict[str, Any]:
    return _registry().get_live_feedback_summary()


def get_optimization_history(limit: int = 10) -> list[dict[str, Any]]:
    return _registry().get_optimization_history(limit=limit)
