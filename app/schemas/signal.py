from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.enums import AssetClass, MarketType, Side, SignalStatus, StopTrigger


class EntrySpec(BaseModel):
    type: Literal["market", "price", "range"] = "range"
    price: float | None = None
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def validate_values(self) -> "EntrySpec":
        if self.type == "price" and self.price is None:
            raise ValueError("price entry requires price")
        if self.type == "range":
            if self.min is None or self.max is None:
                raise ValueError("range entry requires min and max")
            if self.min > self.max:
                self.min, self.max = self.max, self.min
        return self


class StopSpec(BaseModel):
    price: float
    trigger: StopTrigger = StopTrigger.PRICE_TOUCH
    timeframe: str | None = None

    @model_validator(mode="after")
    def validate_timeframe(self) -> "StopSpec":
        if self.trigger == StopTrigger.CANDLE_CLOSE and not self.timeframe:
            raise ValueError("candle-close stop requires a timeframe")
        return self


class NormalizedSignal(BaseModel):
    strategy_id: str = "telegram-copy"
    strategy_version: str = "0.1.0"
    source: str = "telegram"
    source_id: str | None = None
    source_message_id: str | None = None
    timestamp: datetime
    asset_class: AssetClass = AssetClass.CRYPTO
    market_type: MarketType = MarketType.SPOT
    symbol: str
    side: Side
    quote: str | None = None
    entry: EntrySpec | None = None
    stop: StopSpec | None = None
    targets: list[float] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: SignalStatus = SignalStatus.PARSED
    raw_text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseSignalRequest(BaseModel):
    text: str
    timestamp: datetime | None = None
    source_id: str | None = None
    source_message_id: str | None = None
    default_quote: str = "USDT"


class ParseSignalResponse(BaseModel):
    signal: NormalizedSignal | None
    confidence: float
    warnings: list[str] = Field(default_factory=list)
