"""Tests for dashboard API: position normalization and close endpoint."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_PATH = PROJECT_ROOT / "api" / "dashboard_api.py"

_spec = importlib.util.spec_from_file_location("dashboard_api_under_test", API_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
app = _mod.app
_normalize_position = _mod._normalize_position


@pytest.fixture
def client():
    return TestClient(app)


# ── Position normalization ──────────────────────────────────────────────────


class TestNormalizePosition:
    def test_lowercase_fields(self):
        pos = {
            "id": 1,
            "positionId": 1,
            "symbolName": "US500",
            "side": "buy",
            "volume": 1.0,
            "entryPrice": 5000.0,
            "stopLoss": 4990.0,
            "takeProfit": 5020.0,
            "profit": 12.5,
        }
        out = _normalize_position(pos)
        assert out["id"] == 1
        assert out["positionId"] == 1
        assert out["symbol"] == "US500"
        assert out["side"] == "BUY"
        assert out["volume"] == 1.0
        assert out["entryPrice"] == 5000.0
        assert out["stopLoss"] == 4990.0
        assert out["takeProfit"] == 5020.0
        assert out["pnl"] == 12.5

    def test_pascalcase_fields(self):
        pos = {
            "Id": 2,
            "PositionId": 2,
            "SymbolName": "EURUSD",
            "Side": "sell",
            "Volume": 0.5,
            "EntryPrice": 1.0850,
            "StopLoss": 1.09,
            "TakeProfit": 1.07,
            "Profit": -8.3,
        }
        out = _normalize_position(pos)
        assert out["id"] == 2
        assert out["symbol"] == "EURUSD"
        assert out["side"] == "SELL"
        assert out["entryPrice"] == 1.085
        assert out["pnl"] == -8.3

    def test_gross_profit_fallback_for_pnl(self):
        pos = {
            "id": 3,
            "symbolName": "US500",
            "side": "buy",
            "volume": 0.1,
            "grossProfit": 45.0,
        }
        out = _normalize_position(pos)
        assert out["pnl"] == 45.0

    def test_unrealized_profit_fallback(self):
        pos = {
            "id": 4,
            "symbolName": "XAUUSD",
            "side": "sell",
            "volume": 0.2,
            "unrealizedProfit": -15.0,
        }
        out = _normalize_position(pos)
        assert out["pnl"] == -15.0

    def test_missing_pnl_defaults_to_zero(self):
        pos = {
            "id": 5,
            "symbolName": "US500",
            "side": "buy",
            "volume": 1.0,
        }
        out = _normalize_position(pos)
        assert out["pnl"] == 0.0

    def test_entry_price_fallback(self):
        pos = {
            "id": 6,
            "symbolName": "US500",
            "side": "buy",
            "volume": 1.0,
            "openPrice": 5001.0,
        }
        out = _normalize_position(pos)
        assert out["entryPrice"] == 5001.0

    def test_empty_position_returns_empty_dict(self):
        out = _normalize_position({})
        assert out["pnl"] == 0.0
        assert out["volume"] == 0
        assert out["side"] == ""

    def test_side_normalization_long_short(self):
        assert _normalize_position({"id": 1, "Side": "Long"})["side"] == "BUY"
        assert _normalize_position({"id": 1, "Side": "Short"})["side"] == "SELL"

    def test_position_id_fallback_chain(self):
        pos = {"Id": 99, "symbolName": "US500"}
        out = _normalize_position(pos)
        assert out["id"] == 99
        assert out["positionId"] == 99


# ── Close position endpoint ─────────────────────────────────────────────────


class TestClosePositionEndpoint:
    def test_close_position_success(self, client):
        mock_mcp = AsyncMock()
        mock_mcp.close_position.return_value = {"closed": True}
        with patch.object(_mod, "mcp", mock_mcp):
            response = client.post("/api/positions/42/close", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        mock_mcp.close_position.assert_called_once_with(42)

    def test_close_position_failure(self, client):
        mock_mcp = AsyncMock()
        mock_mcp.close_position.side_effect = RuntimeError("MCP error")
        with patch.object(_mod, "mcp", mock_mcp):
            response = client.post("/api/positions/42/close", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "MCP error" in body["error"]

    def test_close_position_no_mcp(self, client):
        with patch.object(_mod, "mcp", None):
            response = client.post("/api/positions/42/close", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["error"] == "MCP not connected"
