from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field

from app.schemas.signal import NormalizedSignal


class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class SignalBacktestRequest(BaseModel):
    signal: NormalizedSignal
    candles: list[Candle] = Field(min_length=1)
    candle_timeframe: str = "1m"
    conservative_intrabar: bool = True


class TargetHit(BaseModel):
    target: float
    hit: bool
    hit_at: datetime | None = None


class SignalBacktestResult(BaseModel):
    symbol: str
    entry_triggered: bool
    entry_price: float | None = None
    entry_at: datetime | None = None
    targets: list[TargetHit] = Field(default_factory=list)
    stop_hit: bool = False
    stop_at: datetime | None = None
    exit_price: float | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    max_price: float | None = None
    min_price: float | None = None
    duration_minutes: float | None = None
    warnings: list[str] = Field(default_factory=list)
