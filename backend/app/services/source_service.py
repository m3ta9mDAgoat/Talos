from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.enums import SourceStatus
from app.models.source import SourceRecord
from app.schemas.source import SourceCreate
from app.services.runtime_wake import wake_sources


def create_source(db: Session, data: SourceCreate) -> SourceRecord:
    row = SourceRecord(
        name=data.name,
        type=data.type,
        identifier=data.identifier,
        enabled=data.enabled,
        status=SourceStatus.READY,
        config=data.config,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    wake_sources()
    return row


def list_sources(db: Session) -> list[SourceRecord]:
    return db.query(SourceRecord).order_by(SourceRecord.created_at.desc()).all()
