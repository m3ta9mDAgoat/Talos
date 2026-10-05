from __future__ import annotations

import hashlib
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.models.raw_event import RawEventRecord
from app.plugins.contracts import Event


def _dedupe_key(source_identifier: str, event: Event) -> str:
    revision = event.metadata.get("edit_date") or event.timestamp.isoformat()
    event_type = event.metadata.get("event_type", "message")
    raw = f"{event.source}|{source_identifier}|{event.external_id}|{event_type}|{revision}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def persist_events(db: Session, source_identifier: str, events: Iterable[Event]) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for event in events:
        key = _dedupe_key(source_identifier, event)
        if db.query(RawEventRecord.id).filter(RawEventRecord.dedupe_key == key).first():
            skipped += 1
            continue
        row = RawEventRecord(
            dedupe_key=key,
            source_type=event.source,
            source_identifier=source_identifier,
            external_id=event.external_id or key,
            occurred_at=event.timestamp,
            event_type=event.metadata.get("event_type", "message"),
            text=event.payload.get("text"),
            payload=event.payload,
            metadata_json=event.metadata,
        )
        db.add(row)
        inserted += 1
    db.commit()
    return inserted, skipped
