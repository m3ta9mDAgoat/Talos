from __future__ import annotations

from collections import OrderedDict

from sqlalchemy.orm import Session

from app.core.enums import SignalStatus
from app.models.raw_event import RawEventRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.services.signal_parser import telegram_signal_parser


def _latest_message_revisions(db: Session, source: SourceRecord, limit: int) -> list[RawEventRecord]:
    # Fetch newest ingestions first, then keep one current revision per Telegram message.
    rows = (
        db.query(RawEventRecord)
        .filter(RawEventRecord.source_identifier == source.identifier)
        .filter(RawEventRecord.text.isnot(None))
        .order_by(RawEventRecord.ingested_at.desc(), RawEventRecord.id.desc())
        .limit(max(limit * 4, limit))
        .all()
    )
    selected: OrderedDict[str, RawEventRecord] = OrderedDict()
    for row in rows:
        if row.external_id not in selected:
            selected[row.external_id] = row
        if len(selected) >= limit:
            break
    return list(selected.values())


def parse_stored_messages(db: Session, source: SourceRecord, limit: int = 1000) -> dict[str, int]:
    rows = _latest_message_revisions(db, source, limit)
    parsed = needs_review = ignored = created = updated = 0

    for row in rows:
        text = (row.text or "").strip()
        if not text:
            ignored += 1
            continue

        response = telegram_signal_parser.parse(
            text,
            timestamp=row.occurred_at,
            source_id=source.identifier,
            source_message_id=row.external_id,
            default_quote=str(source.config.get("default_quote", "USDT")),
        )
        signal = response.signal
        if signal is None:
            ignored += 1
            continue

        if signal.status == SignalStatus.PARSED:
            parsed += 1
        else:
            needs_review += 1

        existing = (
            db.query(SignalRecord)
            .filter(SignalRecord.strategy_id == signal.strategy_id)
            .filter(SignalRecord.source == signal.source)
            .filter(SignalRecord.source_id == source.identifier)
            .filter(SignalRecord.source_message_id == row.external_id)
            .first()
        )
        values = {
            "strategy_id": signal.strategy_id,
            "strategy_version": signal.strategy_version,
            "source": signal.source,
            "source_id": source.identifier,
            "source_message_id": row.external_id,
            "timestamp": signal.timestamp,
            "symbol": signal.symbol,
            "side": str(signal.side),
            "market_type": str(signal.market_type),
            "confidence": signal.confidence,
            "status": str(signal.status),
            "entry": signal.entry.model_dump(mode="json") if signal.entry else None,
            "stop": signal.stop.model_dump(mode="json") if signal.stop else None,
            "targets": signal.targets,
            "raw_text": signal.raw_text,
            "metadata_json": {
                **signal.metadata,
                "raw_event_id": row.id,
                "warnings": response.warnings,
                "edit_date": row.metadata_json.get("edit_date"),
                "historical_integrity": "edited" if row.metadata_json.get("edit_date") else "current_snapshot",
            },
        }
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
            updated += 1
        else:
            db.add(SignalRecord(**values))
            created += 1

    db.commit()
    return {
        "examined": len(rows),
        "parsed": parsed,
        "needs_review": needs_review,
        "ignored": ignored,
        "created": created,
        "updated": updated,
    }
