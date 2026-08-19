"""Entry point for incremental re-training.

Usage:
    python scripts/run_retrain.py --days 30 --include-live --min-improvement 5
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ctrader_bot.training.retrain import main

if __name__ == "__main__":
    main()
