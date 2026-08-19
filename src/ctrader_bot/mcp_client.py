"""Async client for the local cTrader MCP server (http://127.0.0.1:9876/mcp/).

This server drives the cTrader **desktop app** (78 tools covering charts,
indicators, watchlists, and real trading) rather than a headless broker API.
Two consequences that shape everything below:

1. Trading tools (get_balance, place_market_order, get_positions, ...) act on
   whichever account is currently ACTIVE in the desktop app UI. There is no
   per-call account parameter and no tool to switch accounts programmatically
   — confirmed via scripts/discover_mcp_tools.py. `assert_demo_account()`
   cross-checks get_balance's balance/currency against get_accounts_list to
   best-effort confirm the active account, but this is a secondary guard —
   the primary safety mechanism is the human keeping the demo account
   selected in the app before running anything that trades.
2. get_trendbars returns OHLC + a `volume` field, capped at 1000 bars per
   call — confirmed live against US500 (tick volume, not exchange traded
   volume; typical for a retail CFD/index feed). get_trendbars_range()
   paginates automatically for longer backtest windows.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx2

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

_TIMEFRAME_MAP = {
    "M1": "m1", "M5": "m5", "M15": "m15", "M30": "m30",
    "H1": "h1", "H4": "h4", "D1": "d1", "W1": "w1", "MN1": "mn1",
}


def _normalize_timeframe(tf: str) -> str:
    return _TIMEFRAME_MAP.get(tf.upper(), tf.lower())


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DemoAccountError(RuntimeError):
    """Raised when the currently-active cTrader account can't be confirmed as demo."""


@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class CTraderMCPClient:
    """Wraps one MCP session to the local cTrader server. Use as an async context manager."""

    def __init__(self, url: str):
        self._url = url
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "CTraderMCPClient":
        self._stack = AsyncExitStack()
        # Parse URL to extract port for Host header
        parsed_url = urlparse(self._url)
        port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
        # The cTrader MCP service expects Host header to be 127.0.0.1:<port>
        # regardless of what host we're actually connecting to (e.g. host.docker.internal)
        headers = {"Host": f"127.0.0.1:{port}"}
        http_client = httpx2.AsyncClient(headers=headers)
        read, write = await self._stack.enter_async_context(
            streamable_http_client(self._url, http_client=http_client)
        )
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._session = None
        self._stack = None

    async def _call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        assert self._session is not None, "CTraderMCPClient must be used as `async with CTraderMCPClient(url) as client:`"
        result = await self._session.call_tool(name, args or {})
        if getattr(result, "is_error", getattr(result, "isError", False)):
            raise RuntimeError(f"MCP tool '{name}' failed: {result.content}")
        text = next((c.text for c in result.content if hasattr(c, "text")), None)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # ---- account ----

    async def get_balance(self) -> dict:
        return await self._call("get_balance")

    async def get_accounts_list(self) -> list[dict]:
        return (await self._call("get_accounts_list"))["accounts"]

    async def get_account_statistics(self) -> dict:
        return await self._call("get_account_statistics")

    async def assert_demo_account(self, expected_account_id: int) -> None:
        """Best-effort confirmation that the active desktop-app account is the
        expected demo account, by matching get_balance's balance/currency
        against a unique entry in get_accounts_list. Raises DemoAccountError
        if it cannot establish a confident match, or if the match resolves to
        a live account or a different account id than expected.
        """
        balance_info = await self.get_balance()
        accounts = await self.get_accounts_list()
        candidates = [
            a for a in accounts
            if a["balance"] == balance_info["balance"] and a["currency"] == balance_info.get("depositAsset")
        ]
        if len(candidates) != 1:
            raise DemoAccountError(
                f"Could not uniquely confirm the active account via balance-matching "
                f"({len(candidates)} candidates). Verify manually in the cTrader app "
                f"that account {expected_account_id} (demo) is active before trading."
            )
        active = candidates[0]
        if active["id"] != expected_account_id or active["isLive"]:
            raise DemoAccountError(
                f"Active cTrader account is {active['id']} (isLive={active['isLive']}), "
                f"not the confirmed demo account {expected_account_id}. Refusing to trade. "
                f"Switch accounts in the cTrader desktop app first."
            )

    # ---- market data ----

    async def get_symbol_details(self, symbol: str) -> dict:
        return await self._call("get_symbol_details", {"symbolName": symbol})

    async def get_spot_prices(self, symbol: str) -> dict:
        return await self._call("get_spot_prices", {"symbolName": symbol})

    async def get_symbol_sessions(self, symbol: str) -> dict:
        return await self._call("get_symbol_sessions", {"symbolName": symbol})

    async def get_trendbars(self, symbol: str, timeframe: str, frm: datetime, to: datetime, limit: int = 1000) -> list[Bar]:
        raw = await self._call("get_trendbars", {
            "symbolName": symbol,
            "timeframe": _normalize_timeframe(timeframe),
            "from": _iso(frm),
            "to": _iso(to),
            "limit": limit,
        })
        return [
            Bar(
                timestamp=datetime.strptime(b["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
                open=b["open"], high=b["high"], low=b["low"], close=b["close"],
                volume=b.get("volume", 0.0),
            )
            for b in raw["bars"]
        ]

    async def get_trendbars_range(self, symbol: str, timeframe: str, frm: datetime, to: datetime) -> list[Bar]:
        """Paginates get_trendbars to cover [frm, to). The server returns the most
        recent `limit` bars within a requested window (not the earliest), confirmed
        empirically: a 5-day M1 request returned only the last ~16.7 hours of bars.
        So pagination must walk `to` backward from the end, not `frm` forward.
        """
        all_bars: list[Bar] = []
        window_end = to
        while window_end > frm:
            batch = await self.get_trendbars(symbol, timeframe, frm, window_end, limit=1000)
            if not batch:
                break
            all_bars = batch + all_bars
            first_ts = batch[0].timestamp
            if first_ts >= window_end:
                break  # safety valve against a non-advancing server response
            window_end = first_ts - timedelta(seconds=1)
            if len(batch) < 1000:
                break

        seen: set[datetime] = set()
        deduped: list[Bar] = []
        for b in all_bars:
            if b.timestamp >= frm and b.timestamp not in seen:
                seen.add(b.timestamp)
                deduped.append(b)
        deduped.sort(key=lambda b: b.timestamp)
        return deduped

    # ---- positions / orders ----

    async def get_positions(self) -> list[dict]:
        return (await self._call("get_positions"))["positions"]

    async def get_pending_orders(self) -> list[dict]:
        return (await self._call("get_pending_orders"))["orders"]

    async def get_order_history(self) -> list[dict]:
        return (await self._call("get_order_history"))["trades"]

    async def get_deals(self, symbol: str | None = None, count: int = 50) -> list[dict]:
        args: dict[str, Any] = {"count": count}
        if symbol:
            args["symbolName"] = symbol
        return (await self._call("get_deals", args))["deals"]

    async def calculate_margin(self, symbol: str, volume: float, volume_type: str = "lots") -> dict:
        return await self._call("calculate_margin", {"symbolName": symbol, "volume": volume, "volumeType": volume_type})

    async def place_market_order(
        self, symbol: str, side: str, volume: float, volume_type: str = "lots",
        stop_loss_pips: float | None = None, take_profit_pips: float | None = None,
        label: str | None = None, comment: str | None = None,
    ) -> dict:
        args: dict[str, Any] = {"symbolName": symbol, "side": side, "volume": volume, "volumeType": volume_type}
        if stop_loss_pips is not None:
            args["stopLossPips"] = stop_loss_pips
        if take_profit_pips is not None:
            args["takeProfitPips"] = take_profit_pips
        if label:
            args["label"] = label
        if comment:
            args["comment"] = comment
        return await self._call("place_market_order", args)

    async def amend_position(self, position_id: int, stop_loss: float | None = None, take_profit: float | None = None) -> dict:
        args: dict[str, Any] = {"positionId": position_id}
        if stop_loss is not None:
            args["stopLoss"] = stop_loss
        if take_profit is not None:
            args["takeProfit"] = take_profit
        return await self._call("amend_position", args)

    async def close_position(self, position_id: int) -> dict:
        return await self._call("close_position", {"positionId": position_id})

    async def close_all_positions(self, symbol: str | None = None) -> dict:
        return await self._call("close_all_positions", {"symbolName": symbol} if symbol else {})

    async def cancel_order(self, order_id: int) -> dict:
        return await self._call("cancel_order", {"orderId": order_id})

    async def cancel_all_pending_orders(self) -> dict:
        return await self._call("cancel_all_pending_orders")

    async def get_server_time(self) -> dict:
        return await self._call("get_server_time")
