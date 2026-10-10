from __future__ import annotations

import hashlib
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.models.raw_event import RawEventRecord
from app.plugins.contracts import Event


def dedupe_key_for(source_identifier: str, event: Event) -> str:
    event_type = event.metadata.get("event_type", "message")
    revision = "deleted" if event_type == "delete" else (event.metadata.get("edit_date") or event.timestamp.isoformat())
    raw = f"{event.source}|{source_identifier}|{event.external_id}|{event_type}|{revision}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _chunks(values: list[str], size: int = 500):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def persist_events(db: Session, source_identifier: str, events: Iterable[Event]) -> tuple[int, int]:
    """Persist one Telegram/API batch with O(1) DB round-trips per chunk.

    Earlier Talos versions did one SELECT per message. With Neon that meant ~1000
    network round-trips for a 1000-message Telegram batch and made imports appear
    stuck. This version computes all dedupe keys first, loads existing keys in bulk,
    and inserts the new rows in one transaction.
    """
    prepared: list[tuple[str, Event]] = []
    for event in events:
        prepared.append((dedupe_key_for(source_identifier, event), event))

    if not prepared:
        return 0, 0

    keys = [key for key, _ in prepared]
    existing: set[str] = set()
    for chunk in _chunks(keys):
        existing.update(
            key
            for (key,) in db.query(RawEventRecord.dedupe_key)
            .filter(RawEventRecord.dedupe_key.in_(chunk))
            .all()
        )

    rows: list[RawEventRecord] = []
    seen_in_batch: set[str] = set()
    skipped = 0
    for key, event in prepared:
        if key in existing or key in seen_in_batch:
            skipped += 1
            continue
        seen_in_batch.add(key)
        rows.append(
            RawEventRecord(
                dedupe_key=key,
                source_type=event.source,
                source_identifier=source_identifier,
                external_id=event.external_id or key,
                occurred_at=event.timestamp,
                event_type=event.metadata.get("event_type", "message"),
                text=event.payload.get("text"),
                payload={k: v for k, v in event.payload.items() if k != "text"},
                metadata_json=event.metadata,
            )
        )

    if rows:
        db.add_all(rows)
    db.commit()
    return len(rows), skipped


def persist_single_event(db: Session, source_identifier: str, event: Event) -> tuple[RawEventRecord, bool]:
    key = dedupe_key_for(source_identifier, event)
    existing = db.query(RawEventRecord).filter(RawEventRecord.dedupe_key == key).first()
    if existing:
        return existing, False
    row = RawEventRecord(
        dedupe_key=key,
        source_type=event.source,
        source_identifier=source_identifier,
        external_id=event.external_id or key,
        occurred_at=event.timestamp,
        event_type=event.metadata.get("event_type", "message"),
        text=event.payload.get("text"),
        payload={k: v for k, v in event.payload.items() if k != "text"},
        metadata_json=event.metadata,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, True
