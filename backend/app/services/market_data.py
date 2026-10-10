from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.schemas.backtest import Candle


@dataclass(slots=True)
class ResolvedMarket:
    exchange: str
    symbol: str
    market: dict[str, Any]


class CCXTMarketDataResolver:
    """Public historical market-data adapter with exchange fallback and caching."""

    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}
        self._markets: dict[str, dict[str, Any]] = {}
        self._market_locks: dict[str, asyncio.Lock] = {}
        self._fetch_locks: dict[str, asyncio.Lock] = {}
        self._candle_cache: OrderedDict[tuple, list[Candle]] = OrderedDict()
        self._cache_max = 128

    def _exchange(self, exchange_id: str):
        if exchange_id not in self._instances:
            import ccxt

            cls = getattr(ccxt, exchange_id, None)
            if cls is None:
                raise ValueError(f"Unsupported CCXT exchange: {exchange_id}")
            self._instances[exchange_id] = cls({"enableRateLimit": True, "timeout": 20000, "options": {"defaultType": "spot"}})
        return self._instances[exchange_id]

    async def _load_markets(self, exchange_id: str) -> dict[str, Any]:
        if exchange_id in self._markets:
            return self._markets[exchange_id]
        lock = self._market_locks.setdefault(exchange_id, asyncio.Lock())
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
            if quote == "USD":
                candidates += [f"{base}/USDT", f"{base}/USDC"]
        return list(dict.fromkeys(candidates))

    async def resolve(self, symbol: str, exchanges: list[str]) -> list[ResolvedMarket]:
        # Load all venue market maps concurrently once per process. After warm-up this
        # becomes an in-memory dictionary lookup.
        loaded = await asyncio.gather(
            *(self._load_markets(exchange_id) for exchange_id in exchanges),
            return_exceptions=True,
        )
        found: list[ResolvedMarket] = []
        for exchange_id, markets in zip(exchanges, loaded):
            if isinstance(markets, Exception):
                continue
            for candidate in self._candidate_symbols(symbol):
                market = markets.get(candidate)
                if not market:
                    continue
                if market.get("spot") is False or market.get("active") is False:
                    continue
                found.append(ResolvedMarket(exchange_id, candidate, market))
                break
        return found

    @staticmethod
    def _day_floor(value: datetime) -> datetime:
        value = value.astimezone(timezone.utc)
        return value.replace(hour=0, minute=0, second=0, microsecond=0)

    def _cache_get(self, key: tuple) -> list[Candle] | None:
        value = self._candle_cache.get(key)
        if value is not None:
            self._candle_cache.move_to_end(key)
        return value

    def _cache_put(self, key: tuple, value: list[Candle]) -> None:
        self._candle_cache[key] = value
        self._candle_cache.move_to_end(key)
        while len(self._candle_cache) > self._cache_max:
            self._candle_cache.popitem(last=False)

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

        # Day-rounded cache means several same-day recommendations for the same coin
        # reuse one market-data download.
        cache_start = self._day_floor(start)
        cache_end = self._day_floor(end) + timedelta(days=1)
        key = (exchange_id, symbol, timeframe, cache_start.isoformat(), cache_end.isoformat())
        cached = self._cache_get(key)
        if cached is not None:
            return [c for c in cached if start <= c.timestamp <= end]

        since = int(cache_start.timestamp() * 1000)
        end_ms = int(cache_end.timestamp() * 1000)
        tf_ms = int(ex.parse_timeframe(timeframe) * 1000)
        output: list[Candle] = []
        seen: set[int] = set()
        page_limit = 1000
        fetch_lock = self._fetch_locks.setdefault(exchange_id, asyncio.Lock())

        while since <= end_ms and len(output) < max_bars:
            try:
                # CCXT exchange objects keep rate-limit state. Serialize individual
                # calls per venue, while still allowing different venues in parallel.
                async with fetch_lock:
                    rows = await asyncio.to_thread(ex.fetch_ohlcv, symbol, timeframe, since, page_limit)
            except Exception:
                return []
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

        output = sorted(output, key=lambda c: c.timestamp)
        self._cache_put(key, output)
        return [c for c in output if start <= c.timestamp <= end]


market_data_resolver = CCXTMarketDataResolver()
