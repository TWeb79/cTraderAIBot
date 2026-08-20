"""SQLite-backed trade journal and strategy digest store.

Single source of truth for trade history, reflections, and periodic
strategy digests. Used by both the live runner and the backtest runner,
and exposed read-only through the dashboard API.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel


class TradeDecision(BaseModel):
    action: str
    confidence: float
    entry_type: str
    entry_price: float | None = None
    stop_loss: float
    take_profit: float
    risk_reward_ratio: float
    reasoning: str
    invalidation_condition: str


class TradeReflection(BaseModel):
    outcome: str
    r_multiple: float
    what_matched_expectation: str
    what_diverged: str
    lesson: str
    setup_tag: str
    pnl: float = 0.0


class TradeRecord(BaseModel):
    opened_at: str
    closed_at: str
    symbol: str
    decision_json: str
    reflection_json: str
    r_multiple: float
    setup_tag: str


class DigestRecord(BaseModel):
    created_at: str
    digest_text: str


class Journal:
    """Thin wrapper around a SQLite file for trade history and digests."""

    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at TEXT,
                closed_at TEXT,
                symbol TEXT,
                decision_json TEXT,
                reflection_json TEXT,
                r_multiple REAL,
                setup_tag TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS digests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                digest_text TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS cycle_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_cycle_at TEXT,
                open_position_ids TEXT
            )
        """)
        self.conn.commit()

    def record_trade(self, decision: TradeDecision, reflection: TradeReflection, symbol: str,
                     opened_at: str | None = None) -> int:
        """opened_at: ISO timestamp of the actual trade entry, if known (the
        live runner now captures this at order-placement time — see
        execution/live_runner.py's _execute_trade). Defaults to "now" (the
        close-time value) when omitted, preserving the exact prior behavior
        for any caller that doesn't pass it (e.g. existing tests/backtest
        tooling), so opened_at == closed_at only when the true entry time
        genuinely isn't available.
        """
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            "INSERT INTO trades (opened_at, closed_at, symbol, decision_json, "
            "reflection_json, r_multiple, setup_tag) VALUES (?,?,?,?,?,?,?)",
            (
                opened_at or now,
                now,
                symbol,
                decision.model_dump_json(),
                reflection.model_dump_json(),
                reflection.r_multiple,
                reflection.setup_tag,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def trades_since_last_digest(self) -> int:
        last = self.conn.execute(
            "SELECT created_at FROM digests ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not last:
            return self.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM trades WHERE opened_at > ?", (last[0],)
        ).fetchone()[0]

    def aggregate_stats(self, limit: int = 50) -> dict[str, Any]:
        """Overall win-rate/avg-R/by-tag stats, plus total_pnl — a straight
        sum of each trade's reflection.pnl (added in the §15.5 journal
        schema fix; defaults to 0.0 for older rows recorded before that
        field existed, so this never raises on a mixed-history database).
        """
        rows = self.conn.execute(
            "SELECT r_multiple, setup_tag, reflection_json FROM trades ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        if not rows:
            return {}
        wins = [r for r, _, _ in rows if r > 0]
        total_pnl = 0.0
        for _, _, reflection_json in rows:
            try:
                total_pnl += float(__import__("json").loads(reflection_json).get("pnl", 0.0) or 0.0)
            except (ValueError, TypeError):
                pass
        stats: dict[str, Any] = {
            "n_trades": len(rows),
            "win_rate": len(wins) / len(rows),
            "avg_r": sum(r for r, _, _ in rows) / len(rows),
            "total_pnl": total_pnl,
        }
        by_tag: dict[str, list[float]] = {}
        for r, tag, _ in rows:
            by_tag.setdefault(tag, []).append(r)
        stats["by_tag"] = {
            tag: {"n": len(vals), "avg_r": sum(vals) / len(vals)}
            for tag, vals in by_tag.items()
        }
        return stats

    def latest_digest(self) -> str:
        row = self.conn.execute(
            "SELECT digest_text FROM digests ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else "No strategy digest yet — this is the first cycle."

    def save_digest(self, text: str):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO digests (created_at, digest_text) VALUES (?,?)",
            (now, text),
        )
        self.conn.commit()

    def get_trades(self, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT opened_at, closed_at, symbol, r_multiple, setup_tag, reflection_json, decision_json "
            "FROM trades ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "opened_at": r[0],
                "closed_at": r[1],
                "symbol": r[2],
                "r_multiple": r[3],
                "setup_tag": r[4],
                "reflection": __import__("json").loads(r[5]),
                "decision": __import__("json").loads(r[6]) if r[6] else None,
            }
            for r in rows
        ]

    def save_cycle_state(self, open_position_ids: list[str]):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO cycle_state (id, last_cycle_at, open_position_ids) VALUES (1, ?, ?)",
            (now, __import__("json").dumps(open_position_ids)),
        )
        self.conn.commit()

    def load_cycle_state(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT last_cycle_at, open_position_ids FROM cycle_state WHERE id = 1"
        ).fetchone()
        if not row:
            return None
        return {
            "last_cycle_at": row[0],
            "open_position_ids": __import__("json").loads(row[1]),
        }

    def close(self):
        self.conn.close()
