from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.enums import SourceType
from app.db.session import get_db
from app.models.source import SourceRecord
from app.schemas.source import SourceCreate, SourceRead
from app.services.ingestion import persist_events
from app.services.source_service import create_source, list_sources

router = APIRouter(prefix="/sources", tags=["sources"])


@router.post("", response_model=SourceRead)
def add_source(data: SourceCreate, db: Session = Depends(get_db)) -> SourceRecord:
    return create_source(db, data)


@router.get("", response_model=list[SourceRead])
def get_sources(db: Session = Depends(get_db)) -> list[SourceRecord]:
    return list_sources(db)


@router.post("/{source_id}/import-history")
async def import_history(
    source_id: int,
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> dict:
    source = db.get(SourceRecord, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if source.type != SourceType.TELEGRAM:
        raise HTTPException(status_code=400, detail="Historical import is implemented for Telegram first")
    try:
        from app.plugins.datasources.telegram import TelegramSource

        connector = TelegramSource()
        events = await connector.historical(channel=source.identifier, limit=limit)
        inserted, skipped = persist_events(db, source.identifier, events)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"source_id": source_id, "fetched": len(events), "inserted": inserted, "skipped": skipped}
