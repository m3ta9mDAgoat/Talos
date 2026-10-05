from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from telethon.errors import FloodWaitError

from app.db.session import SessionLocal
from app.models.source import SourceRecord
from app.services.ingestion import persist_events


_tasks: dict[int, asyncio.Task] = {}


def _state(source: SourceRecord) -> dict[str, Any]:
    return dict((source.config or {}).get("full_import") or {})


def status(source_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        state = _state(source)
        running = bool(source_id in _tasks and not _tasks[source_id].done())
        return {
            "source_id": source_id,
            "status": state.get("status", "not_started"),
            "running": running,
            "fetched_total": int(state.get("fetched_total", 0)),
            "inserted_total": int(state.get("inserted_total", 0)),
            "skipped_total": int(state.get("skipped_total", 0)),
            "cursor_id": int(state.get("cursor_id", (source.config or {}).get("history_cursor_id", 0) or 0)),
            "history_complete": bool(state.get("history_complete", (source.config or {}).get("history_complete", False))),
            "batches": int(state.get("batches", 0)),
            "last_error": state.get("last_error"),
            "updated_at": state.get("updated_at"),
        }


def start(source_id: int, *, batch_size: int = 1000) -> dict[str, Any]:
    existing = _tasks.get(source_id)
    if existing and not existing.done():
        return status(source_id)
    _tasks[source_id] = asyncio.create_task(_run(source_id, batch_size=batch_size))
    return status(source_id)


async def _run(source_id: int, *, batch_size: int) -> None:
    from app.plugins.datasources.telegram import TelegramSource

    fetched_total = inserted_total = skipped_total = batches = 0
    retries = 0

    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            return
        previous = _state(source)
        fetched_total = int(previous.get("fetched_total", 0))
        inserted_total = int(previous.get("inserted_total", 0))
        skipped_total = int(previous.get("skipped_total", 0))
        batches = int(previous.get("batches", 0))
        cursor = int(previous.get("cursor_id", (source.config or {}).get("history_cursor_id", 0) or 0))
        source.config = {
            **(source.config or {}),
            "full_import": {
                **previous,
                "status": "running",
                "cursor_id": cursor,
                "history_complete": False,
                "last_error": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        db.commit()
        identifier = source.identifier

    while True:
        try:
            connector = TelegramSource()
            events = list(
                await connector.historical(
                    channel=identifier,
                    limit=batch_size,
                    min_id=cursor,
                    reverse=True,
                )
            )
            retries = 0
        except FloodWaitError as exc:
            await asyncio.sleep(int(getattr(exc, "seconds", 30)) + 1)
            continue
        except Exception as exc:
            retries += 1
            if retries <= 5:
                await asyncio.sleep(min(60, 2 ** retries))
                continue
            with SessionLocal() as db:
                source = db.get(SourceRecord, source_id)
                if source:
                    source.config = {
                        **(source.config or {}),
                        "full_import": {
                            **_state(source),
                            "status": "error",
                            "last_error": str(exc),
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        },
                    }
                    db.commit()
            return

        with SessionLocal() as db:
            source = db.get(SourceRecord, source_id)
            if not source:
                return
            inserted, skipped = persist_events(db, source.identifier, events)
            fetched_total += len(events)
            inserted_total += inserted
            skipped_total += skipped
            batches += 1
            max_id = max((int(event.external_id or 0) for event in events), default=cursor)
            if max_id > cursor:
                cursor = max_id
            complete = len(events) < batch_size
            state = {
                "status": "complete" if complete else "running",
                "fetched_total": fetched_total,
                "inserted_total": inserted_total,
                "skipped_total": skipped_total,
                "cursor_id": cursor,
                "history_complete": complete,
                "batches": batches,
                "last_error": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            source.config = {
                **(source.config or {}),
                "history_cursor_id": cursor,
                "history_complete": complete,
                "full_import": state,
            }
            db.commit()

        if complete:
            return
        await asyncio.sleep(0.35)
