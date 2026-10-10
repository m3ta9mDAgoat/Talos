from __future__ import annotations

import asyncio
import time
import threading
from datetime import datetime, timezone
from typing import Any

from app.db.session import SessionLocal
from app.models.source import SourceRecord
from app.plugins.datasources.telegram import TelegramSource
from app.services.ingestion import persist_single_event
from app.services.live_signal_monitor import ensure_live_simulation, sync_simulation_to_signal
from app.services.notifications import notify_signal, notify_signal_deleted
from app.services.processing import process_raw_event, process_source
from app.services.universal_sources import sync_source
from app.services.runtime_wake import SOURCE_WAKE_EVENT


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_SOURCE_LOCK = threading.RLock()
_SOURCE_STATES: dict[int, dict[str, Any]] = {}


def _mark_source(source_id: int, **values: Any) -> None:
    with _SOURCE_LOCK:
        _SOURCE_STATES[source_id] = {**_SOURCE_STATES.get(source_id, {}), **values}


def stream_status(source_id: int) -> dict[str, Any]:
    """Process-local MTProto stream status; no guess from worker-thread uptime."""
    with _SOURCE_LOCK:
        state = dict(_SOURCE_STATES.get(source_id, {}))
    return {"source_id":source_id,"connected":bool(state.get("connected",False)),
        "last_connected_at":state.get("last_connected_at"),
        "last_event_at":state.get("last_event_at"),
        "last_edit_at":state.get("last_edit_at"),
        "last_event_message_id":state.get("last_event_message_id"),
        "last_error":state.get("last_error"),
        "note":"Connected means this process registered Telegram stream handlers. It does not guarantee all historical edits were observable or that the next event will arrive without interruption."}


async def watch_source(source_id: int) -> None:
    backoff = 2
    while True:
        with SessionLocal() as db:
            source = db.get(SourceRecord, source_id)
            if not source or not source.enabled:
                return
            identifier = str(source.identifier)
        try:
            _mark_source(source_id,connected=False)
            # Catch up once whenever the MTProto stream connects/reconnects. This
            # closes any gap caused by Render sleep/network interruption without
            # polling Telegram + Postgres every minute while the stream is healthy.
            try:
                await sync_source(source_id)
                await asyncio.to_thread(process_source, source_id, force=False)
            except Exception as exc:
                print(f"telegram catchup source {source_id}: {type(exc).__name__}: {exc}", flush=True)
            connector = TelegramSource()
            def connected():
                _mark_source(source_id, connected=True, last_connected_at=utcnow().isoformat(), last_error=None)
            async for event in connector.stream(channel=identifier, on_connected=connected):
                meta = event.metadata or {}
                _mark_source(source_id, last_event_at=utcnow().isoformat(),
                    last_event_message_id=str(event.external_id),
                    **({"last_edit_at":utcnow().isoformat()} if meta.get("event_type")=="edit" else {}))
                with SessionLocal() as db:
                    row, inserted = persist_single_event(db, identifier, event)
                    raw_event_id = int(row.id)
                if not inserted:
                    continue
                result: dict[str, Any] = await asyncio.to_thread(process_raw_event, source_id, raw_event_id)
                signal_id = result.get("signal_id")
                revision_type = result.get("revision_type")
                if revision_type == "delete":
                    try:
                        await notify_signal_deleted(source_id, str(event.external_id))
                    except Exception as exc:
                        print(f"notify delete {source_id}/{event.external_id}: {exc}", flush=True)
                    continue
                if not signal_id:
                    continue
                if revision_type == "message" and result.get("created"):
                    ensure_live_simulation(int(signal_id), source_id)
                    try:
                        await notify_signal(int(signal_id), "new")
                    except Exception as exc:
                        print(f"notify new signal {signal_id}: {exc}", flush=True)
                elif revision_type == "edit":
                    ensure_live_simulation(int(signal_id), source_id)
                    sync_simulation_to_signal(int(signal_id))
                    try:
                        await notify_signal(int(signal_id), "edit", raw_event_id=raw_event_id)
                    except Exception as exc:
                        print(f"notify edit signal {signal_id}: {exc}", flush=True)
                backoff = 2
        except asyncio.CancelledError:
            _mark_source(source_id, connected=False, last_error="Stream task cancelled")
            raise
        except Exception as exc:
            _mark_source(source_id, connected=False, last_error=f"{type(exc).__name__}: {exc}")
            print(f"telegram stream source {source_id}: {type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)


async def stream_supervisor(stop_event: asyncio.Event | None = None) -> None:
    tasks: dict[int, asyncio.Task] = {}
    wanted: set[int] = set()
    next_refresh = 0.0
    try:
        while not (stop_event and stop_event.is_set()):
            now = time.monotonic()
            # Source configuration lives in SQL, but there is no reason to query
            # it every 15 seconds. API source changes set SOURCE_WAKE_EVENT; the
            # 30-minute fallback is only for out-of-band DB edits/recovery.
            if not wanted or SOURCE_WAKE_EVENT.is_set() or now >= next_refresh:
                SOURCE_WAKE_EVENT.clear()
                try:
                    with SessionLocal() as db:
                        rows = db.query(SourceRecord).filter(SourceRecord.enabled == True).all()
                        wanted = {
                            int(x.id)
                            for x in rows
                            if ((x.config or {}).get("adapter") or str(getattr(x.type, "value", x.type))) == "telegram"
                        }
                    next_refresh = time.monotonic() + 1800.0
                except Exception as exc:
                    print(f"telegram supervisor source refresh: {type(exc).__name__}: {exc}", flush=True)
                    next_refresh = time.monotonic() + 300.0
            for sid in list(tasks):
                if sid not in wanted or tasks[sid].done():
                    task = tasks.pop(sid)
                    if not task.done():
                        task.cancel()
                    _mark_source(sid, connected=False)
            for sid in wanted:
                if sid not in tasks:
                    tasks[sid] = asyncio.create_task(watch_source(sid), name=f"telegram-live-{sid}")
            await asyncio.sleep(5)
    finally:
        for task in tasks.values():
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
