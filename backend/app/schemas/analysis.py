from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AnalyzeSourceRequest(BaseModel):
    timeframe: str = "1h"
    horizon_days: int = Field(default=14, ge=1, le=90)
    fee_bps: float = Field(default=10.0, ge=0, le=200)
    slippage_bps: float = Field(default=5.0, ge=0, le=200)
    portfolio_fraction: float = Field(default=0.10, gt=0, le=1)
    require_stop: bool = True
    exclude_edited: bool = True
    conservative_intrabar: bool = True
    max_signals: int = Field(default=500, ge=1, le=5000)
    exchanges: list[str] = Field(
        default_factory=lambda: ["binance", "okx", "bybit", "bitget", "kucoin", "mexc"]
    )


class AnalysisRunRead(BaseModel):
    id: int
    source_id: int
    status: str
    stage: str
    progress: float
    config: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AnalysisTradeRead(BaseModel):
    id: int
    signal_id: int
    timestamp: datetime
    symbol: str
    exchange: str | None = None
    market_symbol: str | None = None
    status: str
    return_pct: float | None = None
    result: dict[str, Any] = Field(default_factory=dict)


class FullImportStatus(BaseModel):
    source_id: int
    status: str
    running: bool = False
    fetched_total: int = 0
    inserted_total: int = 0
    skipped_total: int = 0
    cursor_id: int = 0
    history_complete: bool = False
    batches: int = 0
    estimated_total: int | None = None
    last_error: str | None = None
    updated_at: str | None = None


class ReviewMessageRead(BaseModel):
    raw_event_id: int
    external_id: str
    occurred_at: datetime
    text: str | None
    predicted_class: str
    confidence: float
    manual_class: str | None = None
    reasons: list[str] = Field(default_factory=list)


class ManualLabelRequest(BaseModel):
    message_class: str
