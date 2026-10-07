from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.session import SessionLocal
from app.models.signal import SignalRecord
from app.models.trading import LiveSignalSimulationRecord
from app.services.brokers import best_venue, ohlcv
from app.services.notifications import notification_config, notify_simulation_event


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_live_simulation(signal_id: int, source_id: int | None = None) -> LiveSignalSimulationRecord | None:
    with SessionLocal() as db:
        signal = db.get(SignalRecord, signal_id)
        if not signal or signal.source != "telegram" or signal.status != "parsed" or signal.side != "buy":
            return None
        existing = db.query(LiveSignalSimulationRecord).filter(LiveSignalSimulationRecord.signal_id == signal_id).first()
        if existing:
            return existing
        targets = [{"price": float(x), "hit": False, "hit_at": None} for x in (signal.targets or [])]
        row = LiveSignalSimulationRecord(
            signal_id=signal.id,
            source_id=source_id,
            symbol=signal.symbol,
            side=signal.side,
            status="waiting_entry",
            target_state_json=targets,
            state_json={"signal_timestamp": signal.timestamp.isoformat()},
        )
        db.add(row); db.commit(); db.refresh(row)
        return row


def _entry_fill(entry: dict[str, Any] | None, candle: dict[str, Any]) -> float | None:
    if not entry or entry.get("type") == "market":
        return float(candle["open"])
    if entry.get("type") == "price" and entry.get("price") is not None:
        price = float(entry["price"])
        return price if float(candle["low"]) <= price <= float(candle["high"]) else None
    if entry.get("type") == "range":
        lo, hi = float(entry.get("min")), float(entry.get("max"))
        op, low, high = float(candle["open"]), float(candle["low"]), float(candle["high"])
        if lo <= op <= hi:
            return op
        if op > hi and low <= hi:
            return hi
        if op < lo and high >= lo:
            return lo
    return None


def _tf_seconds(tf: str) -> int:
    n = int(tf[:-1]); unit = tf[-1].lower()
    return n * {"m": 60, "h": 3600, "d": 86400}[unit]


async def _closed_stop_breached(broker: str, symbol: str, stop: dict[str, Any], state: dict[str, Any]) -> tuple[bool, str | None, float | None]:
    tf = str(stop.get("timeframe") or "4h")
    rows = (await ohlcv(broker, symbol, tf, limit=3)).get("candles") or []
    now_ms = int(utcnow().timestamp() * 1000)
    tf_ms = _tf_seconds(tf) * 1000
    closed = [x for x in rows if int(x["timestamp"]) + tf_ms <= now_ms]
    if not closed:
        return False, None, None
    candle = closed[-1]
    key = str(candle["timestamp"])
    if state.get("last_stop_candle") == key:
        return False, key, float(candle["close"])
    state["last_stop_candle"] = key
    return float(candle["close"]) < float(stop["price"]), key, float(candle["close"])


async def advance_live_simulations(limit: int = 50) -> dict[str, Any]:
    cfg = notification_config()
    horizon = timedelta(days=int(cfg.get("simulation_horizon_days", 14)))
    with SessionLocal() as db:
        ids = [x.id for x in db.query(LiveSignalSimulationRecord).filter(LiveSignalSimulationRecord.status.in_(["waiting_entry", "open"])).order_by(LiveSignalSimulationRecord.id.asc()).limit(limit).all()]
    checked = events = 0
    for sim_id in ids:
        checked += 1
        notify: list[tuple[str, str | None]] = []
        pending: list[tuple[str, str | None, str]] = []
        try:
            with SessionLocal() as db:
                sim = db.get(LiveSignalSimulationRecord, sim_id)
                signal = db.get(SignalRecord, sim.signal_id) if sim else None
                if not sim or not signal:
                    continue
                now = utcnow()
                if now - sim.started_at > horizon:
                    sim.status = "expired"; sim.completed_at = now; sim.updated_at = now; db.commit()
                    notify.append(("expired", None))
                else:
                    broker = sim.broker_id
                    market_symbol = sim.market_symbol
                    if not broker:
                        venue = await best_venue(signal.symbol)
                        best = venue.get("best")
                        if not best:
                            sim.last_checked_at = now; sim.state_json = {**(sim.state_json or {}), "last_error": "no_public_market"}; db.commit(); continue
                        broker = str(best["broker_id"]); market_symbol = str(best["symbol"])
                        sim.broker_id = broker; sim.market_symbol = market_symbol
                    since = sim.last_checked_at or max(sim.started_at, now - timedelta(minutes=10))
                    rows = (await ohlcv(broker, market_symbol, "1m", limit=30, since=int(since.timestamp() * 1000))).get("candles") or []
                    if not rows:
                        sim.last_checked_at = now; db.commit(); continue
                    targets = [dict(x) for x in (sim.target_state_json or [])]
                    state = dict(sim.state_json or {})
                    for candle in rows:
                        ts = datetime.fromtimestamp(int(candle["timestamp"]) / 1000, tz=timezone.utc)
                        if sim.entry_at is None:
                            fill = _entry_fill(signal.entry, candle)
                            if fill is not None:
                                sim.entry_at = ts; sim.entry_price = float(fill); sim.status = "open"
                                sim.max_price = float(fill); sim.min_price = float(fill)
                                notify.append(("entry", f"Fill: {fill}"))
                            else:
                                sim.current_price = float(candle["close"])
                                continue
                        sim.current_price = float(candle["close"])
                        sim.max_price = max(float(sim.max_price or sim.entry_price), float(candle["high"]))
                        sim.min_price = min(float(sim.min_price or sim.entry_price), float(candle["low"]))
                        stop = signal.stop or {}
                        if stop and str(stop.get("trigger", "price_touch")) == "price_touch" and float(candle["low"]) <= float(stop["price"]):
                            sim.stop_hit = True; sim.stop_at = ts; sim.status = "stopped"; sim.completed_at = ts
                            notify.append(("stop", f"Stop: {stop['price']}")); break
                        for idx, target in enumerate(targets):
                            if target.get("hit"):
                                continue
                            if float(candle["high"]) >= float(target["price"]):
                                target["hit"] = True; target["hit_at"] = ts.isoformat()
                                notify.append(("target", f"TP{idx+1}: {target['price']}"))
                        if targets and all(x.get("hit") for x in targets):
                            sim.status = "success"; sim.completed_at = ts; notify.append(("complete", "All targets reached")); break
                    if sim.status == "open" and signal.stop and str((signal.stop or {}).get("trigger")) == "candle_close":
                        breached, _, close = await _closed_stop_breached(broker, market_symbol, signal.stop, state)
                        if breached:
                            sim.stop_hit = True; sim.stop_at = now; sim.status = "stopped"; sim.completed_at = now
                            notify.append(("stop", f"Candle close stop: {close}"))
                    if sim.entry_price:
                        sim.mfe_pct = (float(sim.max_price or sim.entry_price) - sim.entry_price) / sim.entry_price * 100
                        sim.mae_pct = (float(sim.min_price or sim.entry_price) - sim.entry_price) / sim.entry_price * 100
                    sim.target_state_json = targets; sim.state_json = state; sim.last_checked_at = now; sim.updated_at = now
                    notified = set(sim.notified_events_json or [])
                    for event, detail in notify:
                        key = f"{event}:{detail or ''}"
                        if key not in notified:
                            notified.add(key); pending.append((event, detail, key))
                    sim.notified_events_json = sorted(notified)
                    db.commit()
            for event, detail, _ in pending:
                await notify_simulation_event(sim_id, event, detail); events += 1
        except Exception as exc:
            with SessionLocal() as db:
                sim = db.get(LiveSignalSimulationRecord, sim_id)
                if sim:
                    sim.state_json = {**(sim.state_json or {}), "last_error": f"{type(exc).__name__}: {exc}"}; sim.last_checked_at = utcnow(); db.commit()
    return {"checked": checked, "notifications": events}
