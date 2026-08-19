"""CLI entry point for training mechanisms.

Usage:
    python scripts/run_training.py optimize --days 60 --symbol US500
    python scripts/run_training.py simulate --days 60 --symbol US500 --analyze-failures
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ctrader_bot.training.optimizer import main as optimizer_main
from ctrader_bot.training.simulator import main as simulator_main
from ctrader_bot.training import retrain as retrain_module


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Training mechanisms for cTrader strategy")
    subparsers = parser.add_subparsers(dest="command", required=True)

    opt_parser = subparsers.add_parser("optimize", help="Run parameter grid search optimizer")
    opt_parser.add_argument("--days", type=int, default=30)
    opt_parser.add_argument("--symbol", type=str, default=None)
    opt_parser.add_argument("--top", type=int, default=10)
    opt_parser.add_argument("--output", type=str, default=None)
    opt_parser.add_argument("--include-live", action="store_true", help="Blend registry live feedback into scoring")
    opt_parser.add_argument("--registry-path", type=str, default=None)
    opt_parser.set_defaults(func=optimizer_main)

    sim_parser = subparsers.add_parser("simulate", help="Run simulated trading engine")
    sim_parser.add_argument("--days", type=int, default=30)
    sim_parser.add_argument("--symbol", type=str, default=None)
    sim_parser.add_argument("--analyze-failures", action="store_true")
    sim_parser.add_argument("--output", type=str, default=None)
    sim_parser.add_argument("--report", type=str, default=None)
    sim_parser.set_defaults(func=simulator_main)

    retrain_parser = subparsers.add_parser("retrain", help="Incremental retrain around best params")
    retrain_parser.add_argument("--days", type=int, default=30)
    retrain_parser.add_argument("--symbol", type=str, default=None)
    retrain_parser.add_argument("--include-live", action="store_true", help="Blend registry live feedback into scoring")
    retrain_parser.add_argument("--min-improvement", type=float, default=5.0, help="Min percent improvement to accept new params")
    retrain_parser.add_argument("--registry-path", type=str, default=None)
    retrain_parser.set_defaults(func=retrain_module.main)

    args = parser.parse_args()
    args.func()


if __name__ == "__main__":
    main()
