from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from app.core.config import settings
from app.services.security import get_secret, get_runtime

SUPPORTED = ["binance", "okx", "bybit", "bitget", "kucoin", "mexc"]


def _credentials(broker_id: str) -> dict[str, str | None]:
    stored = get_secret(broker_id)
    return {
        "apiKey": stored.get("api_key") or getattr(settings, f"{broker_id}_api_key", None),
        "secret": stored.get("api_secret") or getattr(settings, f"{broker_id}_api_secret", None),
        "password": stored.get("password") or getattr(settings, f"{broker_id}_password", None),
    }


def credentials_present(broker_id: str) -> bool:
    c = _credentials(broker_id)
    if broker_id in {"okx", "kucoin", "bitget"}:
        return bool(c["apiKey"] and c["secret"] and c["password"])
    return bool(c["apiKey"] and c["secret"])


def make_exchange(broker_id: str, *, private: bool = False):
    import ccxt
    cls = getattr(ccxt, broker_id, None)
    if cls is None: raise ValueError(f"Unsupported exchange {broker_id}")
    opts: dict[str, Any] = {"enableRateLimit": True, "timeout": 20000, "options": {"defaultType": "spot"}}
    if private:
        creds = _credentials(broker_id)
        if not credentials_present(broker_id): raise RuntimeError(f"{broker_id} credentials are incomplete")
        opts.update({k:v for k,v in creds.items() if v})
    return cls(opts)


async def public_status(broker_id: str) -> dict:
    try:
        ex = make_exchange(broker_id)
        markets = await asyncio.to_thread(ex.load_markets)
        return {"broker_id": broker_id, "public_ok": True, "markets": len(markets), "credentials": credentials_present(broker_id)}
    except Exception as exc:
        return {"broker_id": broker_id, "public_ok": False, "markets": 0, "credentials": credentials_present(broker_id), "error": str(exc)}


async def private_status(broker_id: str) -> dict:
    if not credentials_present(broker_id):
        return {"broker_id": broker_id, "connected": False, "reason": "credentials_missing"}
    try:
        ex = make_exchange(broker_id, private=True)
        balance = await asyncio.to_thread(ex.fetch_balance)
        total = {k: float(v) for k,v in (balance.get("total") or {}).items() if v}
        return {"broker_id": broker_id, "connected": True, "balances": total}
    except Exception as exc:
        return {"broker_id": broker_id, "connected": False, "reason": str(exc)}


async def ticker(broker_id: str, symbol: str) -> dict:
    ex = make_exchange(broker_id)
    await asyncio.to_thread(ex.load_markets)
    if symbol not in ex.markets: raise ValueError(f"{symbol} is not listed on {broker_id}")
    t = await asyncio.to_thread(ex.fetch_ticker, symbol)
    bid, ask = t.get("bid"), t.get("ask")
    spread_pct = ((ask-bid)/((ask+bid)/2)*100) if bid and ask and ask+bid else None
    return {"broker_id": broker_id, "symbol": symbol, "last": t.get("last"), "bid": bid, "ask": ask,
            "spread_pct": spread_pct, "base_volume": t.get("baseVolume"), "quote_volume": t.get("quoteVolume"),
            "timestamp": t.get("timestamp")}


async def best_venue(symbol: str, broker_ids: list[str] | None = None) -> dict:
    ids = broker_ids or SUPPORTED
    rows = await asyncio.gather(*(ticker(x, symbol) for x in ids), return_exceptions=True)
    valid = [r for r in rows if isinstance(r, dict) and r.get("last")]
    def score(r: dict) -> float:
        spread = r.get("spread_pct") if r.get("spread_pct") is not None else 10.0
        vol = float(r.get("quote_volume") or 0.0)
        return spread * 1000 - min(vol / 1_000_000, 1000)
    valid.sort(key=score)
    return {"symbol": symbol, "best": valid[0] if valid else None, "venues": valid}


async def place_live_order(*, broker_id: str, symbol: str, side: str, amount: float, order_type: str="market", price: float|None=None) -> dict:
    runtime = get_runtime("trading", {"live_trading_enabled": False})
    enabled = bool(runtime.get("live_trading_enabled", False) or settings.live_trading_enabled)
    if not enabled:
        raise RuntimeError("Live trading is disabled in Talos Settings.")
    ex = make_exchange(broker_id, private=True)
    params = {}
    order = await asyncio.to_thread(ex.create_order, symbol, order_type, side, amount, price, params)
    return order


async def ohlcv(broker_id: str, symbol: str, timeframe: str = "1h", limit: int = 200, since: int | None = None) -> dict:
    ex = make_exchange(broker_id)
    await asyncio.to_thread(ex.load_markets)
    if symbol not in ex.markets:
        raise ValueError(f"{symbol} is not listed on {broker_id}")
    if not ex.has.get("fetchOHLCV"):
        raise ValueError(f"{broker_id} does not expose OHLCV through CCXT")
    rows = await asyncio.to_thread(ex.fetch_ohlcv, symbol, timeframe, since, min(max(int(limit), 10), 1000))
    return {
        "broker_id": broker_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": [
            {"timestamp": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5] or 0)}
            for r in rows
        ],
    }
