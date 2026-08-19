"""Offline journal review: generates a strategy digest via the Anthropic API.

This script is **never** imported by the live runner. It is meant to be run
manually (or on a cron) after trading sessions to produce a written analysis
of recent trades.

Usage:
    python scripts/run_journal_review.py [--limit 50] [--output digest.md]

Requires ANTHROPIC_API_KEY in .env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ctrader_bot.config import load_secrets
from ctrader_bot.journal.store import Journal


def build_prompt(trades: list[dict], stats: dict, previous_digest: str) -> str:
    trades_summary = json.dumps(trades, indent=2, default=str)
    stats_summary = json.dumps(stats, indent=2, default=str)
    return f"""You are a trading coach reviewing a deterministic strategy journal.

Recent trades (most recent first):
{trades_summary}

Aggregate stats:
{stats_summary}

Previous strategy digest:
{previous_digest}

Write a concise strategy digest (2-4 sentences) that highlights:
1. Which setup_tags are performing well or poorly.
2. Any regime-dependent patterns.
3. Actionable adjustments for the next session.

Base this on the aggregate pattern across trades, not any single trade."""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate offline journal digest via Anthropic API")
    parser.add_argument("--limit", type=int, default=50, help="Number of recent trades to review")
    parser.add_argument("--output", type=str, default=None, help="Write digest to file (default: stdout)")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    db_path = str(PROJECT_ROOT / "trade_journal.sqlite3")
    journal = Journal(db_path)

    trades = journal.get_trades(limit=args.limit)
    stats = journal.aggregate_stats(limit=args.limit)
    previous_digest = journal.latest_digest()

    if not trades:
        print("No trades in journal to review.")
        sys.exit(0)

    prompt = build_prompt(trades, stats, previous_digest)

    try:
        import anthropic
        client = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        digest = message.content[0].text
    except Exception as e:
        print(f"Anthropic API call failed: {e}")
        sys.exit(1)

    journal.save_digest(digest)

    if args.output:
        Path(args.output).write_text(digest)
        print(f"Digest written to {args.output}")
    else:
        print("=" * 60)
        print("STRATEGY DIGEST")
        print("=" * 60)
        print(digest)


if __name__ == "__main__":
    main()
