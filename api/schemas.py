"""Pydantic response models for the dashboard API."""

from __future__ import annotations

from pydantic import BaseModel


class BarResponse(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class AccountSnapshot(BaseModel):
    equity: float
    balance: float
    daily_pnl: float = 0.0


class PositionResponse(BaseModel):
    id: int | str
    symbol: str
    side: str
    volume: float
    entry_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    pnl: float | None = None


class TradeRecordResponse(BaseModel):
    opened_at: str
    closed_at: str
    symbol: str
    r_multiple: float | None = None
    setup_tag: str | None = None
    reflection: dict | None = None


class DigestResponse(BaseModel):
    digest: str
    stats: dict


class VersionResponse(BaseModel):
    version: str
    build_time: str


class HealthResponse(BaseModel):
    status: str
