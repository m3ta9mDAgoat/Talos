from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


from app.schemas.backtest import Candle


@dataclass(slots=True)
class ResolvedMarket:
    exchange: str
    symbol: str
    market: dict[str, Any]


class CCXTMarketDataResolver:
    """Public market-data adapter with exchange fallback.

    No API keys are required for the historical backtest path. Live execution is a
    separate adapter and remains disabled by default.
    """

    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}
        self._markets: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _exchange(self, exchange_id: str):
        if exchange_id not in self._instances:
            import ccxt
            cls = getattr(ccxt, exchange_id, None)
            if cls is None:
                raise ValueError(f"Unsupported CCXT exchange: {exchange_id}")
            self._instances[exchange_id] = cls({"enableRateLimit": True, "timeout": 20000})
        return self._instances[exchange_id]

    async def _load_markets(self, exchange_id: str) -> dict[str, Any]:
        if exchange_id in self._markets:
            return self._markets[exchange_id]
        lock = self._locks.setdefault(exchange_id, asyncio.Lock())
        async with lock:
            if exchange_id in self._markets:
                return self._markets[exchange_id]
            ex = self._exchange(exchange_id)
            markets = await asyncio.to_thread(ex.load_markets)
            self._markets[exchange_id] = markets
            return markets

    @staticmethod
    def _candidate_symbols(symbol: str) -> list[str]:
        clean = symbol.upper().replace("|", "/").replace("-", "/").replace("_", "/")
        parts = clean.split("/")
        candidates = [clean]
        if len(parts) == 2:
            base, quote = parts
            # Some channels use USD generically while spot venues quote USDT/USDC.
            if quote == "USD":
                candidates += [f"{base}/USDT", f"{base}/USDC"]
        return list(dict.fromkeys(candidates))

    async def resolve(self, symbol: str, exchanges: list[str]) -> list[ResolvedMarket]:
        found: list[ResolvedMarket] = []
        for exchange_id in exchanges:
            try:
                markets = await self._load_markets(exchange_id)
            except Exception:
                continue
            for candidate in self._candidate_symbols(symbol):
                market = markets.get(candidate)
                if not market:
                    continue
                if market.get("spot") is False:
                    continue
                if market.get("active") is False:
                    continue
                found.append(ResolvedMarket(exchange_id, candidate, market))
                break
        return found

    async def candles(
        self,
        *,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        max_bars: int = 20000,
    ) -> list[Candle]:
        ex = self._exchange(exchange_id)
        if not ex.has.get("fetchOHLCV"):
            return []
        since = int(start.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
        tf_ms = int(ex.parse_timeframe(timeframe) * 1000)
        output: list[Candle] = []
        seen: set[int] = set()

        # A conservative page size works across the selected venues.
        page_limit = 500
        while since <= end_ms and len(output) < max_bars:
            try:
                rows = await asyncio.to_thread(ex.fetch_ohlcv, symbol, timeframe, since, page_limit)
            except Exception:
                return output
            if not rows:
                break
            advanced = False
            for row in rows:
                ts = int(row[0])
                if ts in seen:
                    continue
                if ts > end_ms:
                    break
                seen.add(ts)
                output.append(
                    Candle(
                        timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5] or 0.0),
                    )
                )
                if ts >= since:
                    advanced = True
            last_ts = int(rows[-1][0])
            next_since = last_ts + tf_ms
            if not advanced or next_since <= since:
                break
            since = next_since
            if last_ts >= end_ms:
                break
        return sorted(output, key=lambda c: c.timestamp)


market_data_resolver = CCXTMarketDataResolver()
