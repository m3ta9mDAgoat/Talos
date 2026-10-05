from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.enums import SourceStatus, SourceType
from app.db.session import get_db
from app.models.raw_event import RawEventRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.schemas.analysis import FullImportStatus
from app.schemas.source import (
    ImportResult,
    ParseStoredResult,
    RawMessageRead,
    SourceAccessResult,
    SourceCreate,
    SourceRead,
    StoredSignalRead,
)
from app.services.full_import import start as start_full_import
from app.services.full_import import status as full_import_status
from app.services.ingestion import persist_events
from app.services.source_service import create_source, list_sources
from app.services.stored_signal_service import parse_stored_messages

router = APIRouter(prefix="/sources", tags=["sources"])


def _get_source(db: Session, source_id: int) -> SourceRecord:
    source = db.get(SourceRecord, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


def _require_telegram(source: SourceRecord) -> None:
    if source.type != SourceType.TELEGRAM:
        raise HTTPException(status_code=400, detail="This action is implemented for Telegram sources first")


@router.post("", response_model=SourceRead)
def add_source(data: SourceCreate, db: Session = Depends(get_db)) -> SourceRecord:
    return create_source(db, data)


@router.get("", response_model=list[SourceRead])
def get_sources(db: Session = Depends(get_db)) -> list[SourceRecord]:
    return list_sources(db)


@router.post("/{source_id}/test-access", response_model=SourceAccessResult)
async def test_access(source_id: int, db: Session = Depends(get_db)) -> SourceAccessResult:
    source = _get_source(db, source_id)
    _require_telegram(source)
    checked_at = datetime.now(timezone.utc)
    try:
        from app.plugins.datasources.telegram import TelegramSource

        info = await TelegramSource().access_info(channel=source.identifier)
        source.status = SourceStatus.READY
        source.config = {
            **(source.config or {}),
            "access_verified": True,
            "access_checked_at": checked_at.isoformat(),
            "telegram_entity_id": info.get("entity_id"),
            "telegram_title": info.get("title"),
            "telegram_username": info.get("username"),
            "telegram_kind": info.get("kind"),
            "last_error": None,
        }
        db.commit()
        return SourceAccessResult(source_id=source_id, checked_at=checked_at, **info)
    except Exception as exc:
        source.status = SourceStatus.ERROR
        source.config = {
            **(source.config or {}),
            "access_verified": False,
            "access_checked_at": checked_at.isoformat(),
            "last_error": str(exc),
        }
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{source_id}/import-history", response_model=ImportResult)
async def import_history(
    source_id: int,
    limit: int = Query(default=1000, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> ImportResult:
    source = _get_source(db, source_id)
    _require_telegram(source)
    try:
        from app.plugins.datasources.telegram import TelegramSource

        cursor = int((source.config or {}).get("history_cursor_id") or 0)
        events = list(await TelegramSource().historical(channel=source.identifier, limit=limit, min_id=cursor, reverse=True))
        inserted, skipped = persist_events(db, source.identifier, events)
        max_id = max((int(event.external_id or 0) for event in events), default=cursor)
        history_complete = len(events) < limit
        source.config = {
            **(source.config or {}),
            "history_cursor_id": max_id,
            "history_complete": history_complete,
            "last_history_import_at": datetime.now(timezone.utc).isoformat(),
            "last_error": None,
        }
        source.status = SourceStatus.READY
        db.commit()
    except Exception as exc:
        source.status = SourceStatus.ERROR
        source.config = {**(source.config or {}), "last_error": str(exc)}
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportResult(
        source_id=source_id,
        fetched=len(events),
        inserted=inserted,
        skipped=skipped,
        history_cursor_id=max_id,
        history_complete=history_complete,
    )


@router.post("/{source_id}/import-full", response_model=FullImportStatus)
async def import_full(source_id: int, db: Session = Depends(get_db)) -> FullImportStatus:
    source = _get_source(db, source_id)
    _require_telegram(source)
    return FullImportStatus(**start_full_import(source_id))


@router.get("/{source_id}/import-full/status", response_model=FullImportStatus)
def import_full_status(source_id: int, db: Session = Depends(get_db)) -> FullImportStatus:
    source = _get_source(db, source_id)
    _require_telegram(source)
    return FullImportStatus(**full_import_status(source_id))


@router.post("/{source_id}/sync", response_model=ImportResult)
async def sync_latest(
    source_id: int,
    limit: int = Query(default=250, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> ImportResult:
    source = _get_source(db, source_id)
    _require_telegram(source)
    try:
        from app.plugins.datasources.telegram import TelegramSource

        events = list(await TelegramSource().latest(channel=source.identifier, limit=limit))
        inserted, skipped = persist_events(db, source.identifier, events)
        source.config = {
            **(source.config or {}),
            "last_sync_at": datetime.now(timezone.utc).isoformat(),
            "last_error": None,
        }
        source.status = SourceStatus.READY
        db.commit()
    except Exception as exc:
        source.status = SourceStatus.ERROR
        source.config = {**(source.config or {}), "last_error": str(exc)}
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportResult(source_id=source_id, fetched=len(events), inserted=inserted, skipped=skipped)


@router.get("/{source_id}/messages", response_model=list[RawMessageRead])
def get_messages(
    source_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[RawMessageRead]:
    source = _get_source(db, source_id)
    rows = (
        db.query(RawEventRecord)
        .filter(RawEventRecord.source_identifier == source.identifier)
        .order_by(RawEventRecord.occurred_at.desc(), RawEventRecord.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        RawMessageRead(
            id=row.id,
            external_id=row.external_id,
            occurred_at=row.occurred_at,
            ingested_at=row.ingested_at,
            event_type=row.event_type,
            text=row.text,
            edit_date=(row.metadata_json or {}).get("edit_date"),
            metadata=row.metadata_json or {},
        )
        for row in rows
    ]


@router.post("/{source_id}/parse-signals", response_model=ParseStoredResult)
def parse_source_signals(
    source_id: int,
    limit: int = Query(default=5000, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> ParseStoredResult:
    source = _get_source(db, source_id)
    _require_telegram(source)
    counts = parse_stored_messages(db, source, limit=limit)
    source.config = {
        **(source.config or {}),
        "last_parse_at": datetime.now(timezone.utc).isoformat(),
    }
    db.commit()
    return ParseStoredResult(source_id=source_id, **counts)


@router.get("/{source_id}/signals", response_model=list[StoredSignalRead])
def get_source_signals(
    source_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[StoredSignalRead]:
    source = _get_source(db, source_id)
    rows = (
        db.query(SignalRecord)
        .filter(SignalRecord.source_id == source.identifier)
        .order_by(SignalRecord.timestamp.desc(), SignalRecord.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [
        StoredSignalRead(
            id=row.id,
            source_message_id=row.source_message_id,
            timestamp=row.timestamp,
            symbol=row.symbol,
            side=row.side,
            confidence=row.confidence,
            status=row.status,
            entry=row.entry,
            stop=row.stop,
            targets=row.targets or [],
            raw_text=row.raw_text,
            metadata=row.metadata_json or {},
        )
        for row in rows
    ]
