from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Iterable

from app.core.enums import MarketType, Side


@dataclass(slots=True)
class Event:
    source: str
    timestamp: datetime
    payload: dict[str, Any]
    external_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Decision:
    strategy_id: str
    strategy_version: str
    timestamp: datetime
    symbol: str
    side: Side
    market_type: MarketType = MarketType.SPOT
    confidence: float | None = None
    entry: dict[str, Any] | None = None
    stop: dict[str, Any] | None = None
    targets: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class DataSource(ABC):
    @abstractmethod
    async def historical(self, **kwargs: Any) -> Iterable[Event]: ...

    @abstractmethod
    async def stream(self, **kwargs: Any) -> AsyncIterator[Event]: ...


class FeaturePlugin(ABC):
    @abstractmethod
    async def transform(self, events: list[Event], **kwargs: Any) -> dict[str, Any]: ...


class Strategy(ABC):
    strategy_id: str
    version: str

    @abstractmethod
    async def on_events(self, events: list[Event], features: dict[str, Any]) -> list[Decision]: ...

    def optimization_space(self) -> dict[str, Any]:
        return {}


class MarketDataProvider(ABC):
    @abstractmethod
    async def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime, **kwargs: Any): ...


class ExecutionBroker(ABC):
    @abstractmethod
    async def place(self, decision: Decision, **kwargs: Any) -> dict[str, Any]: ...

    @abstractmethod
    async def cancel(self, order_id: str, **kwargs: Any) -> dict[str, Any]: ...


class Optimizer(ABC):
    @abstractmethod
    async def optimize(self, strategy: Strategy, objective: str, **kwargs: Any) -> dict[str, Any]: ...
