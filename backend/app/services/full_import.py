from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from telethon.errors import FloodWaitError

from app.db.session import SessionLocal
from app.models.source import SourceRecord
from app.services.ingestion import persist_events


def _state(source: SourceRecord) -> dict[str, Any]:
    return dict((source.config or {}).get("full_import") or {})


def status(source_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        state = _state(source)
        return {
            "source_id": source_id,
            "status": state.get("status", "not_started"),
            # Import is deliberately request-stepped. The browser performs the next
            # batch, so Render never owns one fragile multi-minute background task.
            "running": False,
            "fetched_total": int(state.get("fetched_total", 0)),
            "inserted_total": int(state.get("inserted_total", 0)),
            "skipped_total": int(state.get("skipped_total", 0)),
            "cursor_id": int(state.get("cursor_id", (source.config or {}).get("history_cursor_id", 0) or 0)),
            "history_complete": bool(state.get("history_complete", (source.config or {}).get("history_complete", False))),
            "batches": int(state.get("batches", 0)),
            "estimated_total": state.get("estimated_total"),
            "last_error": state.get("last_error"),
            "updated_at": state.get("updated_at"),
        }


async def prepare(source_id: int, *, batch_size: int = 1000) -> dict[str, Any]:
    from app.plugins.datasources.telegram import TelegramSource

    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        previous = _state(source)
        identifier = source.identifier
        complete = bool(previous.get("history_complete", (source.config or {}).get("history_complete", False)))
        if complete:
            return status(source_id)

    estimated_total = previous.get("estimated_total")
    if estimated_total is None:
        try:
            estimated_total = await TelegramSource().total_messages(channel=identifier)
        except Exception:
            estimated_total = None

    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        previous = _state(source)
        cursor = int(previous.get("cursor_id", (source.config or {}).get("history_cursor_id", 0) or 0))
        new_state = {
            **previous,
            "status": "ready",
            "cursor_id": cursor,
            "history_complete": False,
            "estimated_total": estimated_total,
            "batch_size": batch_size,
            "last_error": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        source.config = {**(source.config or {}), "full_import": new_state}
        db.commit()
    return status(source_id)


async def step(source_id: int, *, batch_size: int = 1000) -> dict[str, Any]:
    """Fetch and store one resumable Telegram history batch.

    One HTTP call == one bounded batch. This survives Render restarts and avoids
    proxy 502s caused by long background jobs. The frontend automatically repeats
    this endpoint until history_complete=true.
    """
    from app.plugins.datasources.telegram import TelegramSource

    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        previous = _state(source)
        if previous.get("history_complete") or (source.config or {}).get("history_complete"):
            return status(source_id)
        identifier = source.identifier
        cursor = int(previous.get("cursor_id", (source.config or {}).get("history_cursor_id", 0) or 0))
        fetched_total = int(previous.get("fetched_total", 0))
        inserted_total = int(previous.get("inserted_total", 0))
        skipped_total = int(previous.get("skipped_total", 0))
        batches = int(previous.get("batches", 0))
        estimated_total = previous.get("estimated_total")

    last_error: str | None = None
    events = []
    for retry in range(6):
        try:
            events = list(
                await TelegramSource().historical(
                    channel=identifier,
                    limit=batch_size,
                    min_id=cursor,
                    reverse=True,
                )
            )
            last_error = None
            break
        except FloodWaitError as exc:
            await asyncio.sleep(min(65, int(getattr(exc, "seconds", 30)) + 1))
        except Exception as exc:
            last_error = str(exc)
            if retry >= 5:
                break
            await asyncio.sleep(min(20, 2 ** retry))

    if last_error is not None:
        with SessionLocal() as db:
            source = db.get(SourceRecord, source_id)
            if source:
                previous = _state(source)
                source.config = {
                    **(source.config or {}),
                    "full_import": {
                        **previous,
                        "status": "error",
                        "last_error": last_error,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
                db.commit()
        return status(source_id)

    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        inserted, skipped = persist_events(db, source.identifier, events)
        fetched_total += len(events)
        inserted_total += inserted
        skipped_total += skipped
        batches += 1
        max_id = max((int(event.external_id or 0) for event in events), default=cursor)
        if max_id > cursor:
            cursor = max_id
        complete = len(events) < batch_size
        new_state = {
            "status": "complete" if complete else "ready",
            "fetched_total": fetched_total,
            "inserted_total": inserted_total,
            "skipped_total": skipped_total,
            "cursor_id": cursor,
            "history_complete": complete,
            "batches": batches,
            "estimated_total": estimated_total,
            "batch_size": batch_size,
            "last_error": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        source.config = {
            **(source.config or {}),
            "history_cursor_id": cursor,
            "history_complete": complete,
            "full_import": new_state,
        }
        db.commit()
    return status(source_id)
