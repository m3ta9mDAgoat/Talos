from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import cast, String

from app.db.session import SessionLocal
from app.models.signal import SignalRecord
from app.models.trading import LiveSignalSimulationRecord
from app.services.brokers import best_venue, ohlcv
from app.services.notifications import notify_simulation_event
from app.services.signal_horizon import aware, decide_entry_window, decide_horizon, settlement
from app.services.runtime_wake import wake_simulations


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_live_simulation(signal_id: int, source_id: int | None = None) -> LiveSignalSimulationRecord | None:
    with SessionLocal() as db:
        signal = db.get(SignalRecord, signal_id)
        if not signal or signal.source != "telegram" or signal.status != "parsed" or signal.side != "buy":
            return None
        existing = db.query(LiveSignalSimulationRecord).filter(LiveSignalSimulationRecord.signal_id == signal_id).first()
        if existing:
            existing_id = int(existing.id)
        else:
            # If the first parseable signal was an edit, no original trade was
            # observable. Start the live paper clock when Talos actually saw it.
            # Do not invent a trade at the original post timestamp.
            meta = dict(signal.metadata_json or {})
            first_observed = utcnow()
            revised_at = meta.get("edit_date") if meta.get("revision_type") in {"edit", "snapshot"} else None
            effective_from = first_observed
            if revised_at:
                try:
                    effective_from = max(first_observed, datetime.fromisoformat(str(revised_at).replace("Z", "+00:00")).astimezone(timezone.utc))
                except (ValueError, TypeError):
                    effective_from = first_observed
            targets = [{"price": float(x), "hit": False, "hit_at": None} for x in (signal.targets or [])]
            row = LiveSignalSimulationRecord(
                signal_id=signal.id,
                source_id=source_id,
                symbol=signal.symbol,
                side=signal.side,
                status="waiting_entry",
                target_state_json=targets,
                started_at=effective_from,
                state_json={"signal_timestamp": signal.timestamp.isoformat(), "observed_from": effective_from.isoformat(),
                    "first_parseable_was_edit": bool(revised_at), "signal_revision_raw_event_id": meta.get("raw_event_id"),
                    "horizon_policy": "adaptive_horizon_1", "planned_exit": "pending_entry"},
            )
            db.add(row); db.commit(); db.refresh(row)
            wake_simulations()
            return row
    # Existing simulations follow signal edits without resetting already-hit TPs.
    result = sync_simulation_to_signal(signal_id)
    wake_simulations()
    return result


def sync_simulation_to_signal(signal_id: int) -> LiveSignalSimulationRecord | None:
    """Apply the latest recommendation revision to an existing live simulation.

    Hit-state is preserved by target price. New targets start un-hit and are only
    evaluated against candles observed after the edit, so Talos never awards a TP
    retroactively because an admin added it after price had already passed it.
    """
    with SessionLocal() as db:
        signal = db.get(SignalRecord, signal_id)
        sim = db.query(LiveSignalSimulationRecord).filter(LiveSignalSimulationRecord.signal_id == signal_id).first()
        if not signal or not sim:
            return sim
        state=dict(sim.state_json or {})
        if sim.entry_at is None:
            if sim.symbol != signal.symbol:
                sim.symbol = signal.symbol
                sim.broker_id = None
                sim.market_symbol = None
                sim.current_price = None
            sim.side = signal.side
        else:
            if sim.symbol != signal.symbol:
                state["symbol_change_after_entry_ignored"] = {"simulation_symbol": sim.symbol, "edited_signal_symbol": signal.symbol, "at": utcnow().isoformat()}
            if sim.side != signal.side:
                state["side_change_after_entry_ignored"] = {"simulation_side": sim.side, "edited_signal_side": signal.side, "at": utcnow().isoformat()}
        old = {float(x.get("price")): dict(x) for x in (sim.target_state_json or []) if x.get("price") is not None}
        targets=[]
        for value in (signal.targets or []):
            price=float(value)
            previous=old.get(price)
            targets.append(previous if previous is not None else {"price":price,"hit":False,"hit_at":None})
        sim.target_state_json=targets
        state["signal_revision_raw_event_id"]=(signal.metadata_json or {}).get("raw_event_id")
        state["signal_revision_type"]=(signal.metadata_json or {}).get("revision_type")
        state["signal_edit_date"]=(signal.metadata_json or {}).get("edit_date")
        sim.state_json=state
        sim.updated_at=utcnow()
        db.commit(); db.refresh(sim)
        wake_simulations()
        return sim

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


async def _closed_stop_breached(broker: str, symbol: str, stop: dict[str, Any],
                                state: dict[str, Any], since: datetime,
                                until: datetime) -> tuple[bool, str | None, float | None]:
    """Scan only closes OBSERVABLE after entry and no later than planned exit."""
    tf = str(stop.get("timeframe") or "4h")
    tf_ms = _tf_seconds(tf) * 1000
    since_ms = int(aware(since).timestamp() * 1000)
    until_ms = int(aware(until).timestamp() * 1000)
    rows = (await ohlcv(broker, symbol, tf, limit=100,
                        since=max(0, since_ms - tf_ms))).get("candles") or []
    last_key = None
    last_close = None
    for candle in sorted(rows, key=lambda x: int(x["timestamp"])):
        close_ms = int(candle["timestamp"]) + tf_ms
        if not (since_ms <= close_ms <= until_ms):
            continue
        key = str(candle["timestamp"])
        if state.get("last_stop_candle") and int(key) <= int(state["last_stop_candle"]):
            continue
        last_key, last_close = key, float(candle["close"])
        state["last_stop_candle"] = key
        if last_close < float(stop["price"]):
            return True, key, last_close
    return False, last_key, last_close


def _finish(sim: LiveSignalSimulationRecord, state: dict, *, reason: str,
            exit_at: datetime | None = None, exit_price: float | None = None) -> dict:
    """Persist immutable evaluation evidence even for missing/no-entry outcomes."""
    end = aware(exit_at or utcnow())
    result: dict[str, Any] = {"reason": reason, "evaluated_at": utcnow().isoformat(),
                               "exit_at": end.isoformat(), "policy": state.get('hold_plan') or state.get('entry_plan')}
    if sim.entry_price and exit_price is not None and sim.side == 'buy':
        result.update(settlement(float(sim.entry_price), float(exit_price)))
        sim.status = 'stopped' if reason in {'stop_touch', '4h_closed_stop'} else 'success' if reason == 'all_targets' else 'evaluated'
    elif reason == 'entry_timeout':
        result['outcome'] = 'no_entry'
        sim.status = 'entry_timeout'
    else:
        result['outcome'] = 'unverifiable'
        sim.status = 'unverifiable'
    state['evaluation'] = result
    sim.completed_at = end
    sim.updated_at = utcnow()
    return result


async def advance_live_simulations(limit: int = 50) -> dict[str, Any]:
    """Check all closed candles chronologically, then evaluate at a frozen adaptive exit time."""
    with SessionLocal() as db:
        ids = [x.id for x in db.query(LiveSignalSimulationRecord).filter(
            LiveSignalSimulationRecord.status.in_(["waiting_entry", "open"])
        ).order_by(LiveSignalSimulationRecord.id.asc()).limit(limit).all()]
    checked = events = 0
    for sim_id in ids:
        checked += 1
        pending: list[tuple[str, str | None]] = []
        try:
            with SessionLocal() as db:
                sim = db.get(LiveSignalSimulationRecord, sim_id)
                signal = db.get(SignalRecord, sim.signal_id) if sim else None
                if not sim or not signal or sim.status not in ('waiting_entry', 'open'):
                    continue
                now = utcnow()
                state = dict(sim.state_json or {})
                broker, market_symbol = sim.broker_id, sim.market_symbol
                if not broker:
                    venue = await best_venue(sim.symbol)
                    best = venue.get('best')
                    if not best:
                        state['last_error'] = 'no_public_market'
                        state.setdefault('market_data_missing_since', now.isoformat())
                        # Do not pretend an unpriced trade has won or lost.
                        sim.state_json = state; sim.updated_at = now; db.commit()
                        continue
                    broker, market_symbol = str(best['broker_id']), str(best['symbol'])
                    sim.broker_id, sim.market_symbol = broker, market_symbol
                stop = signal.stop or {}
                # Fetch old-to-new, not just latest: after downtime process pending
                # candles in bounded chunks rather than silently skipping price moves.
                start = aware(sim.started_at)
                cursor = aware(sim.last_checked_at) if sim.last_checked_at else start - timedelta(minutes=1)
                # A prior buggy cursor or missing pages must not prevent recovery
                # of the final candle at the frozen deadline after a restart.
                # Revisit only the last 2 minutes; hit targets are deduplicated.
                if sim.status == 'open' and state.get('hold_plan'):
                    frozen_end = datetime.fromisoformat(state['hold_plan']['deadline'])
                    if now >= frozen_end and cursor >= frozen_end - timedelta(minutes=1):
                        cursor = frozen_end - timedelta(minutes=2)
                rows = (await ohlcv(broker, market_symbol, '1m', limit=500,
                                    since=max(0, int((cursor + timedelta(seconds=1)).timestamp() * 1000)))).get('candles') or []
                rows = sorted(rows, key=lambda x: int(x['timestamp']))
                # Only completed candles. First usable candle starts on or after observation time.
                closed = [c for c in rows if (t := datetime.fromtimestamp(int(c['timestamp'])/1000, tz=timezone.utc)) >= start
                          and t > cursor and t + timedelta(minutes=1) <= now]
                if sim.entry_at is None and 'entry_plan' not in state:
                    history = (await ohlcv(broker, market_symbol, '1m', limit=121,
                                since=max(0, int((start-timedelta(minutes=121)).timestamp()*1000)))).get('candles') or []
                    state['entry_plan'] = decide_entry_window(signal.entry, history, start)
                if not closed:
                    sim.state_json = {**state, 'last_error': 'waiting_for_closed_market_candles'}
                    sim.updated_at = now; db.commit(); continue
                targets = [dict(t) for t in (sim.target_state_json or [])]
                deadline_entry = datetime.fromisoformat(state['entry_plan']['entry_deadline']) if 'entry_plan' in state else None
                processed_ts = None
                for candle in closed:
                    ts = datetime.fromtimestamp(int(candle['timestamp'])/1000, tz=timezone.utc)
                    processed_ts = ts
                    close_time = ts + timedelta(minutes=1)
                    if sim.entry_at is None:
                        if deadline_entry and ts >= deadline_entry:
                            _finish(sim, state, reason='entry_timeout', exit_at=deadline_entry)
                            pending.append(('evaluated', 'No entry fill before adaptive entry deadline'))
                            break
                        fill = _entry_fill(signal.entry, candle)
                        if fill is None:
                            sim.current_price = float(candle['close'])
                            continue
                        sim.entry_at = ts; sim.entry_price = float(fill); sim.status = 'open'
                        sim.max_price = float(fill); sim.min_price = float(fill)
                        # Use only candles completed BEFORE the entry. On sparse
                        # API data the fallback is recorded explicitly.
                        history = (await ohlcv(broker, market_symbol, '1m', limit=121,
                                    since=max(0, int((ts-timedelta(minutes=121)).timestamp()*1000)))).get('candles') or []
                        state['hold_plan'] = decide_horizon(float(fill), [t['price'] for t in targets], stop, history, ts)
                        state['planned_exit'] = state['hold_plan']['deadline']
                        pending.append(('entry', f"Entry: {fill}; selected duration: {state['hold_plan']['holding_minutes']} min; review at {state['planned_exit']}"))
                    plan = state.get('hold_plan')
                    if not plan:
                        continue
                    end_at = datetime.fromisoformat(plan['deadline'])
                    # At deadline use only the last fully closed minute candle
                    # and never use an OHLC bar containing future price movement.
                    if ts >= end_at or close_time > end_at:
                        break
                    state['last_before_deadline'] = {'at': close_time.isoformat(), 'close': float(candle['close'])}
                    sim.current_price = float(candle['close'])
                    sim.max_price = max(float(sim.max_price or sim.entry_price), float(candle['high']))
                    sim.min_price = min(float(sim.min_price or sim.entry_price), float(candle['low']))
                    if stop and str(stop.get('trigger', 'price_touch')) == 'price_touch' and float(candle['low']) <= float(stop['price']):
                        sim.stop_hit = True; sim.stop_at = ts
                        result = _finish(sim, state, reason='stop_touch', exit_at=ts, exit_price=float(stop['price']))
                        pending.append(('stop', f"Stop hit. Net result: {result.get('net_return_pct')}%"))
                        break
                    for idx,target in enumerate(targets):
                        if not target.get('hit') and float(candle['high']) >= float(target['price']):
                            target['hit'], target['hit_at'] = True, ts.isoformat()
                            pending.append(('target', f"TP{idx+1}: {target['price']}"))
                    if targets and all(x.get('hit') for x in targets):
                        # Closed-candle stop, if any, takes priority when it
                        # occurred before this target's observable candle close.
                        breached = False; price = None
                        if stop and str(stop.get('trigger')) == 'candle_close':
                            breached, _, price = await _closed_stop_breached(
                                broker, market_symbol, stop, state, sim.entry_at, close_time)
                        if breached:
                            sim.stop_hit = True; sim.stop_at = close_time
                            result = _finish(sim, state, reason='4h_closed_stop', exit_at=close_time, exit_price=price)
                            pending.append(('stop', f"Closed-candle stop. Net result: {result.get('net_return_pct')}%"))
                        else:
                            result = _finish(sim, state, reason='all_targets', exit_at=ts,
                                             exit_price=float(targets[-1]['price']))
                            pending.append(('evaluated', f"All targets reached. Net result: {result.get('net_return_pct')}%"))
                        break
                # Check closed 4h candles even while the trade is open, but never
                # after the frozen planned exit (avoids future-looking stop).
                if sim.status == 'open' and stop and str(stop.get('trigger')) == 'candle_close':
                    end_at = datetime.fromisoformat(state['hold_plan']['deadline'])
                    last_close_time = datetime.fromtimestamp(int(closed[-1]['timestamp'])/1000, tz=timezone.utc) + timedelta(minutes=1)
                    breached, _, price = await _closed_stop_breached(
                        broker, market_symbol, stop, state, sim.entry_at, min(now, end_at, last_close_time))
                    if breached:
                        sim.stop_hit = True; sim.stop_at = min(now, end_at, last_close_time)
                        result = _finish(sim, state, reason='4h_closed_stop', exit_at=sim.stop_at, exit_price=price)
                        pending.append(('stop', f"Closed-candle stop. Net result: {result.get('net_return_pct')}%"))
                # Wait until the chosen deadline ACTUALLY passes. Use the last
                # wholly-closed 1m price before it; never a candle containing
                # future price movement or a quote from after the deadline.
                if sim.status == 'open' and state.get('hold_plan'):
                    end_at = datetime.fromisoformat(state['hold_plan']['deadline'])
                    if now >= end_at:
                        last = state.get('last_before_deadline') or {}
                        close_at = datetime.fromisoformat(last['at']) if last.get('at') else None
                        if not (close_at and timedelta(0) <= end_at-close_at < timedelta(minutes=2)):
                            # Recover a precise pre-deadline close after Render restarts,
                            # exchange pagination gaps, or sparse processing. Never take
                            # the most recent post-deadline market price as a substitute.
                            end_rows = (await ohlcv(broker, market_symbol, '1m', limit=5,
                                since=max(0, int((end_at-timedelta(minutes=2)).timestamp()*1000)))).get('candles') or []
                            usable = []
                            for bar in end_rows:
                                bar_end = datetime.fromtimestamp(int(bar['timestamp'])/1000, tz=timezone.utc) + timedelta(minutes=1)
                                if timedelta(0) <= end_at - bar_end < timedelta(minutes=2) and bar_end <= now:
                                    usable.append((bar_end, float(bar['close'])))
                            if usable:
                                close_at, close_price = max(usable, key=lambda item: item[0])
                                last = {'at': close_at.isoformat(), 'close': close_price}
                                state['last_before_deadline'] = last
                        if close_at and timedelta(0) <= end_at-close_at < timedelta(minutes=2):
                            result = _finish(sim, state, reason='adaptive_deadline',
                                             exit_at=close_at, exit_price=float(last['close']))
                            pending.append(('evaluated', f"Adaptive horizon completed. Net result: {result.get('net_return_pct')}%"))
                            state.pop('last_error', None)
                        else:
                            state['last_error'] = 'missing_complete_candle_at_adaptive_deadline; awaiting historical market data'
                if sim.entry_price:
                    sim.mfe_pct = (float(sim.max_price or sim.entry_price)-sim.entry_price)/sim.entry_price*100
                    sim.mae_pct = (float(sim.min_price or sim.entry_price)-sim.entry_price)/sim.entry_price*100
                sim.target_state_json = targets
                sim.state_json = state
                if processed_ts is not None:
                    sim.last_checked_at = processed_ts
                sim.updated_at = now
                notified = set(sim.notified_events_json or [])
                outbox = list(state.get('delivery_pending') or [])
                for event, detail in pending:
                    key=f"{event}:{detail or ''}"
                    if key not in notified:
                        notified.add(key)
                        outbox.append({'key': key, 'event': event, 'detail': detail})
                if outbox:
                    state['delivery_pending'] = outbox
                sim.state_json = state
                sim.notified_events_json = sorted(notified)
                db.commit()
        except Exception as exc:
            with SessionLocal() as db:
                sim = db.get(LiveSignalSimulationRecord, sim_id)
                if sim:
                    sim.state_json = {**(sim.state_json or {}), 'last_error': f'{type(exc).__name__}: {str(exc)[:250]}'}
                    sim.updated_at = utcnow(); db.commit()
    delivered, pending_count = await deliver_simulation_outbox(limit=40)
    return {'checked': checked + pending_count, 'notifications': events + delivered, 'pending_delivery': pending_count}


async def deliver_simulation_outbox(limit: int = 40) -> tuple[int, int]:
    """Durably retry simulation outcomes after Telegram outages/redeploys."""
    with SessionLocal() as db:
        # cast works on SQLite JSON and PostgreSQL JSON without schema changes.
        ids = [row.id for row in db.query(LiveSignalSimulationRecord).filter(
            cast(LiveSignalSimulationRecord.state_json, String).like('%"delivery_pending"%')
        ).order_by(LiveSignalSimulationRecord.id.asc()).limit(limit).all()]
    sent = 0
    for sim_id in ids:
        with SessionLocal() as db:
            sim = db.get(LiveSignalSimulationRecord, sim_id)
            state = dict(sim.state_json or {}) if sim else {}
            queue = list(state.get('delivery_pending') or [])
        if not queue:
            continue
        item = queue[0]
        try:
            resp = await notify_simulation_event(sim_id, item['event'], item.get('detail'))
            success = bool(resp.get('ok'))
            error = str(resp.get('skipped', 'delivery returned false')) if not success else ''
        except Exception as exc:
            success, error = False, f'{type(exc).__name__}: {str(exc)[:170]}'
        with SessionLocal() as db:
            sim = db.get(LiveSignalSimulationRecord, sim_id)
            if sim:
                state = dict(sim.state_json or {})
                old = list(state.get('delivery_pending') or [])
                if old and old[0].get('key') == item.get('key'):
                    if success:
                        old.pop(0); sent += 1
                        state.pop('delivery_error', None)
                    else:
                        state['delivery_error'] = error
                    if old:
                        state['delivery_pending'] = old
                    else:
                        state.pop('delivery_pending', None)
                    sim.state_json = state; db.commit()
    with SessionLocal() as db:
        remaining = db.query(LiveSignalSimulationRecord.id).filter(
            cast(LiveSignalSimulationRecord.state_json, String).like('%"delivery_pending"%')
        ).limit(1).first() is not None
    return sent, int(remaining)
