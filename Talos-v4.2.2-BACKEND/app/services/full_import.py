from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from telethon.errors import FloodWaitError

from app.db.session import SessionLocal
from app.models.raw_event import RawEventRecord
from app.models.source import SourceRecord
from app.services.ingestion import persist_events

VERIFY_VERSION = 2


def _state(source: SourceRecord) -> dict[str, Any]:
    return dict((source.config or {}).get("full_import") or {})


def _unique_archived(db, identifier: str) -> int:
    return db.query(RawEventRecord.external_id).filter(RawEventRecord.source_identifier == identifier).distinct().count()


def status(source_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        state = _state(source)
        return {
            "source_id": source_id,
            "status": state.get("status", "not_started"),
            "running": False,
            "fetched_total": int(state.get("fetched_total", 0)),
            "inserted_total": int(state.get("inserted_total", 0)),
            "skipped_total": int(state.get("skipped_total", 0)),
            "cursor_id": int(state.get("cursor_id", 0) or 0),
            "history_complete": bool(state.get("history_complete", False)),
            "batches": int(state.get("batches", 0)),
            "estimated_total": state.get("estimated_total"),
            "last_error": state.get("last_error"),
            "updated_at": state.get("updated_at"),
            "verified_accessible_count": state.get("verified_accessible_count"),
            "unique_archived": _unique_archived(db, source.identifier),
            "verification_version": int(state.get("verification_version", 0)),
        }


async def prepare(source_id: int, *, batch_size: int = 1000) -> dict[str, Any]:
    """Start/resume a deterministic newest→oldest verification scan.

    v1 imported oldest→newest using min_id. v2 intentionally re-scans the full
    accessible history from the newest message backwards. Dedupe makes this safe,
    and it fills any gaps left by an older cursor strategy. The scan is considered
    complete only after Telegram returns an explicit empty page.
    """
    from app.plugins.datasources.telegram import TelegramSource

    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        previous = _state(source)
        identifier = source.identifier
        if int(previous.get("verification_version", 0)) == VERIFY_VERSION and previous.get("history_complete"):
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
        # A new verifier version always starts from newest. Existing rows are deduped.
        reset = int(previous.get("verification_version", 0)) != VERIFY_VERSION
        new_state = {
            **previous,
            "status": "ready",
            "cursor_id": 0 if reset else int(previous.get("cursor_id", 0) or 0),
            "history_complete": False,
            "verified_empty_tail": False,
            "verification_version": VERIFY_VERSION,
            "scan_direction": "newest_to_oldest",
            "estimated_total": estimated_total,
            "batch_size": batch_size,
            "fetched_total": 0 if reset else int(previous.get("fetched_total", 0)),
            "inserted_total": 0 if reset else int(previous.get("inserted_total", 0)),
            "skipped_total": 0 if reset else int(previous.get("skipped_total", 0)),
            "batches": 0 if reset else int(previous.get("batches", 0)),
            "verified_accessible_count": None if reset else previous.get("verified_accessible_count"),
            "last_error": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        source.config = {**(source.config or {}), "history_complete": False, "full_import": new_state}
        db.commit()
    return status(source_id)


async def step(source_id: int, *, batch_size: int = 1000) -> dict[str, Any]:
    from app.plugins.datasources.telegram import TelegramSource

    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        previous = _state(source)
        if int(previous.get("verification_version", 0)) != VERIFY_VERSION:
            raise RuntimeError("Start Import / Verify Full History once to initialize the v2 verifier")
        if previous.get("history_complete") and previous.get("verified_empty_tail"):
            return status(source_id)
        identifier = source.identifier
        cursor = int(previous.get("cursor_id", 0) or 0)
        fetched_total = int(previous.get("fetched_total", 0))
        inserted_total = int(previous.get("inserted_total", 0))
        skipped_total = int(previous.get("skipped_total", 0))
        batches = int(previous.get("batches", 0))
        estimated_total = previous.get("estimated_total")

    last_error: str | None = None
    events = []
    for retry in range(6):
        try:
            events = list(await TelegramSource().historical_before(channel=identifier, limit=batch_size, offset_id=cursor))
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
                source.config = {**(source.config or {}), "full_import": {**previous, "status": "error", "last_error": last_error, "updated_at": datetime.now(timezone.utc).isoformat()}}
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
        # For descending pages the next offset is the oldest ID seen.
        if events:
            ids = [int(event.external_id or 0) for event in events if str(event.external_id or "").isdigit()]
            if ids:
                cursor = min(ids)
        complete = len(events) == 0
        verified_count = fetched_total if complete else None
        new_state = {
            "status": "complete" if complete else "ready",
            "fetched_total": fetched_total,
            "inserted_total": inserted_total,
            "skipped_total": skipped_total,
            "cursor_id": cursor,
            "history_complete": complete,
            "verified_empty_tail": complete,
            "verification_version": VERIFY_VERSION,
            "scan_direction": "newest_to_oldest",
            "verified_accessible_count": verified_count,
            "batches": batches,
            "estimated_total": estimated_total,
            "batch_size": batch_size,
            "last_error": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        source.config = {**(source.config or {}), "history_complete": complete, "full_import": new_state}
        db.commit()
    return status(source_id)
