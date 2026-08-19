"""Entry point for running a backtest.

Usage:
    python scripts/run_backtest.py [--days 30] [--symbol US500]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ctrader_bot.execution.backtest_runner import main

if __name__ == "__main__":
    main()
