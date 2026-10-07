from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.db.session import SessionLocal
from app.models.source import SourceRecord
from app.plugins.datasources.telegram import TelegramSource
from app.services.ingestion import persist_single_event
from app.services.live_signal_monitor import ensure_live_simulation, sync_simulation_to_signal
from app.services.notifications import notify_signal, notify_signal_deleted
from app.services.processing import process_raw_event


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def watch_source(source_id: int) -> None:
    backoff = 2
    while True:
        with SessionLocal() as db:
            source = db.get(SourceRecord, source_id)
            if not source or not source.enabled:
                return
            identifier = str(source.identifier)
        try:
            connector = TelegramSource()
            async for event in connector.stream(channel=identifier):
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
            raise
        except Exception as exc:
            print(f"telegram stream source {source_id}: {type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)


async def stream_supervisor(stop_event: asyncio.Event | None = None) -> None:
    tasks: dict[int, asyncio.Task] = {}
    try:
        while not (stop_event and stop_event.is_set()):
            with SessionLocal() as db:
                rows = db.query(SourceRecord).filter(SourceRecord.enabled == True).all()
                wanted = {
                    int(x.id)
                    for x in rows
                    if ((x.config or {}).get("adapter") or str(getattr(x.type, "value", x.type))) == "telegram"
                }
            for sid in list(tasks):
                if sid not in wanted or tasks[sid].done():
                    task = tasks.pop(sid)
                    if not task.done():
                        task.cancel()
            for sid in wanted:
                if sid not in tasks:
                    tasks[sid] = asyncio.create_task(watch_source(sid), name=f"telegram-live-{sid}")
            await asyncio.sleep(15)
    finally:
        for task in tasks.values():
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks.values(), return_exceptions=True)
