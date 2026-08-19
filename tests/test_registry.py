"""Tests for the persistent parameter registry (training/registry.py)."""

from __future__ import annotations

from pathlib import Path

from ctrader_bot.training.optimizer import _blend_metrics, _build_live_synthetic
from ctrader_bot.training.registry import ParameterRegistry


SAMPLE_PARAMS = {
    "level_proximity_atr_mult": 0.25,
    "breakout_confirm_atr_mult": 0.15,
    "trend_direction_lookback": 20,
    "risk_per_trade_pct": 1.5,
    "min_stop_atr_mult": 0.5,
}


def test_save_load_round_trip(tmp_path: Path):
    reg = ParameterRegistry(tmp_path / "registry.json")
    metrics = {
        "total_return_pct": 15.2,
        "max_drawdown_pct": 3.1,
        "win_rate": 0.65,
        "n_trades": 142,
    }
    reg.save_best_params(SAMPLE_PARAMS, metrics, source="backtest")
    loaded = reg.load_best_params()
    assert loaded == SAMPLE_PARAMS
    assert reg.get_performance() == metrics
    assert reg.path.exists()


def test_regime_specific_params_fallback(tmp_path: Path):
    reg = ParameterRegistry(tmp_path / "registry.json")
    reg.save_best_params(SAMPLE_PARAMS, {"total_return_pct": 1.0}, source="backtest")

    range_params = dict(SAMPLE_PARAMS)
    range_params["level_proximity_atr_mult"] = 0.2
    reg.save_best_params(range_params, {"total_return_pct": 2.0}, source="backtest", regime="RANGE")

    assert reg.load_best_params_by_regime("RANGE")["level_proximity_atr_mult"] == 0.2
    # Unknown regime falls back to global best.
    assert reg.load_best_params_by_regime("TREND") == SAMPLE_PARAMS


def test_live_feedback_aggregation(tmp_path: Path):
    reg = ParameterRegistry(tmp_path / "registry.json")
    reg.append_live_feedback({"setup_tag": "trend_pullback_poc", "regime": "TREND",
                              "r_multiple": 1.5, "pnl": 30.0, "entry_price": 100.0, "atr": 1.0})
    reg.append_live_feedback({"setup_tag": "trend_pullback_poc", "regime": "TREND",
                              "r_multiple": 0.5, "pnl": 10.0, "entry_price": 101.0, "atr": 1.1})
    reg.append_live_feedback({"setup_tag": "range_fade_vah", "regime": "RANGE",
                              "r_multiple": -0.4, "pnl": -8.0, "entry_price": 99.0, "atr": 0.9})

    summary = reg.get_live_feedback_summary()
    assert summary["n_live_trades"] == 3
    assert summary["wins"] == 2
    assert abs(summary["live_win_rate"] - 2 / 3) < 1e-9
    assert summary["by_setup_tag"]["trend_pullback_poc"]["n"] == 2
    assert abs(summary["by_setup_tag"]["trend_pullback_poc"]["avg_r"] - 1.0) < 1e-9
    assert summary["by_regime"]["RANGE"]["n"] == 1


def test_optimization_history_capped(tmp_path: Path):
    reg = ParameterRegistry(tmp_path / "registry.json")
    for i in range(15):
        reg.save_best_params(SAMPLE_PARAMS, {"total_return_pct": float(i)}, source="backtest")
    history = reg.get_optimization_history(limit=10)
    assert len(history) == 10
    # Most recent first.
    assert history[0]["metrics"]["total_return_pct"] == 14.0


def test_persistence_across_instances(tmp_path: Path):
    path = tmp_path / "registry.json"
    reg1 = ParameterRegistry(path)
    reg1.save_best_params(SAMPLE_PARAMS, {"total_return_pct": 7.0}, source="backtest")
    reg1.append_live_feedback({"setup_tag": "x", "regime": "RANGE", "r_multiple": 1.0,
                               "pnl": 5.0, "entry_price": 1.0, "atr": 0.5})

    reg2 = ParameterRegistry(path)
    assert reg2.load_best_params() == SAMPLE_PARAMS
    assert reg2.get_live_feedback_summary()["n_live_trades"] == 1


def test_empty_registry_defaults(tmp_path: Path):
    reg = ParameterRegistry(tmp_path / "registry.json")
    assert reg.load_best_params() == {}
    assert reg.get_live_feedback_summary()["n_live_trades"] == 0
    assert reg.get_optimization_history() == []


def test_build_live_synthetic_expands_weight():
    summary = {
        "by_setup_tag": {
            "tag_a": {"n": 10, "avg_r": 0.8},
            "tag_b": {"n": 5, "avg_r": -0.4},
        }
    }
    synth = _build_live_synthetic(summary, feedback_weight=2.0)
    # 2 synthetic trades per bucket (rounded weight).
    assert synth["n"] == 4
    assert synth["wins"] == 2  # only tag_a avg_r > 0


def test_blend_metrics_combines_backtest_and_live():
    backtest = {
        "n_trades": 10, "win_rate": 0.5, "avg_r": 0.2,
        "total_return_pct": 5.0, "max_drawdown_pct": 2.0,
    }
    live = {"n": 2, "wins": 2, "r_sum": 1.6, "synth_return": 1.6}
    blended = _blend_metrics(backtest, live)
    assert blended["n_trades"] == 12
    assert blended["win_rate"] == (5 + 2) / 12
    assert blended["max_drawdown_pct"] == 2.0
