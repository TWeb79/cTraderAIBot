"""Entry point for live/demo trading.

Usage:
    python scripts/run_live.py [--dry-run] [--symbol US500]

Safety:
    - Requires DEMO_MODE=true in .env unless --force-live is passed.
    - Create data/cache/.kill_switch to stop gracefully.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ctrader_bot.execution.live_runner import main

if __name__ == "__main__":
    main()
