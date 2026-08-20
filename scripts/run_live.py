"""Entry point for live/demo trading.

Usage:
    python scripts/run_live.py [--dry-run | --live] [--symbol US500]

    With neither --dry-run nor --live passed, config.yaml's
    execution.dry_run_default decides (false by default, i.e. live orders).

Safety:
    - DEMO_MODE in .env controls the demo-account assertion at startup
      (a mismatch only prints a warning, it is not a hard stop — the human
      keeping the demo account selected in the cTrader desktop app is the
      primary safety mechanism; see mcp_client.py's module docstring).
    - Create data/cache/.kill_switch to stop gracefully.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ctrader_bot.execution.live_runner import main

if __name__ == "__main__":
    main()
