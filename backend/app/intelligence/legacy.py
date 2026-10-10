"""One-time, non-destructive import of v4.4's initial RSS observations."""
from __future__ import annotations
from datetime import datetime, timezone
from hashlib import sha256
from sqlalchemy import select
from app.db.session import SessionLocal
from app.intelligence.classify import classify, clean_text
from app.intelligence.sources import SOURCE_BY_KEY
from app.models.intelligence import IntelligenceEvent, IntelligenceRevision, IntelligenceSourceState
from app.models.osint import OsintItem

SOURCE_KEYS = {
    "SEC Press Releases": "sec",
    "Federal Reserve": "fed",
    "CISA Advisories": "cisa",
    "BBC World": "bbc_world",
    "BBC Business": "bbc_business",
}
MARKER = "legacy_v44_imported"


def _aware(value):
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


def import_legacy(limit: int = 2000) -> dict:
    """Leave old rows untouched; preserve v4.4's actual first_seen_at time.

    Never claims to reconstruct prior edits that the legacy table did not store.
    A one-row migration marker prevents repeated reads/writes on Render restarts.
    """
    with SessionLocal() as db:
        if db.get(IntelligenceSourceState, MARKER):
            return {"status": "previously_completed"}
        previous = db.scalars(select(OsintItem).order_by(OsintItem.id).limit(limit)).all()
        added = 0
        for item in previous:
            key = SOURCE_KEYS.get(item.source)
            if not key:
                continue
            source = SOURCE_BY_KEY[key]
            ext = (item.url or item.fingerprint or "")[:512]
            if not ext:
                continue
            exists = db.scalar(select(IntelligenceEvent.id).where(
                IntelligenceEvent.source_key == key, IntelligenceEvent.external_id == ext))
            if exists:
                continue
            title = clean_text(item.title, 750)
            summary = clean_text(item.summary, 1600)
            metadata = classify(title, summary, source.category, source.reliability)
            observed = _aware(item.first_seen_at) or datetime.now(timezone.utc)
            content_hash = sha256((title + "\0" + summary).encode()).hexdigest()
            event = IntelligenceEvent(source_key=key, external_id=ext,
                                      category=source.category,
                                      event_type=metadata["event_type"], headline=title,
                                      summary=summary, url=item.url,
                                      content_hash=content_hash,
                                      cluster_key=metadata["cluster_key"],
                                      symbols=metadata["symbols"],
                                      regions=metadata["regions"],
                                      entities=metadata["entities"],
                                      importance=metadata["importance"],
                                      classification_confidence=metadata["classification_confidence"],
                                      source_reliability=source.reliability,
                                      published_at=_aware(item.published_at),
                                      first_seen_at=observed, last_seen_at=observed, revision=1)
            db.add(event)
            db.flush()
            db.add(IntelligenceRevision(event_id=event.id, revision=1,
                                        observed_at=observed, title=title, summary=summary,
                                        content_hash=content_hash))
            added += 1
        db.add(IntelligenceSourceState(source_key=MARKER, category="internal", enabled=False,
                                       last_count=added, last_success_at=datetime.now(timezone.utc)))
        db.commit()
        return {"status": "complete", "migrated": added}
