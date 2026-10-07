from __future__ import annotations

from collections import Counter, OrderedDict

from sqlalchemy.orm import Session

from app.core.enums import MessageClass, SignalStatus
from app.db.session import SessionLocal
from app.models.analysis import MessageLabelRecord
from app.models.raw_event import RawEventRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.services.message_classifier import classifier
from app.services.signal_parser import telegram_signal_parser


def _chunks(values: list, size: int = 500):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _latest_message_revisions(db: Session, source: SourceRecord, limit: int | None) -> list[RawEventRecord]:
    query = (
        db.query(RawEventRecord)
        .filter(RawEventRecord.source_identifier == source.identifier)
        .filter(RawEventRecord.text.isnot(None))
        .order_by(RawEventRecord.ingested_at.desc(), RawEventRecord.id.desc())
    )
    if limit is not None:
        # Edits create revisions, so fetch extra rows while retaining only one revision/message.
        query = query.limit(max(limit * 2, limit))
    rows = query.all()
    selected: OrderedDict[str, RawEventRecord] = OrderedDict()
    for row in rows:
        if row.external_id not in selected:
            selected[row.external_id] = row
        if limit is not None and len(selected) >= limit:
            break
    return list(selected.values())


def parse_stored_messages(db: Session, source: SourceRecord, limit: int | None = 1000) -> dict:
    """Classify + parse archived messages with bulk DB access.

    Classification/regex work is cheap. The old bottleneck was thousands of Neon
    queries (label lookup, signal lookup and stale delete per message). We preload
    those rows once, update them in memory, and issue batched stale deletes.
    """
    rows = _latest_message_revisions(db, source, limit)
    if not rows:
        return {
            "examined": 0,
            "parsed": 0,
            "needs_review": 0,
            "ignored": 0,
            "created": 0,
            "updated": 0,
            "classification": {},
        }

    row_ids = [row.id for row in rows]
    labels: dict[int, MessageLabelRecord] = {}
    for chunk in _chunks(row_ids):
        for label in db.query(MessageLabelRecord).filter(MessageLabelRecord.raw_event_id.in_(chunk)).all():
            labels[label.raw_event_id] = label

    existing_signals = {
        str(signal.source_message_id): signal
        for signal in (
            db.query(SignalRecord)
            .filter(SignalRecord.strategy_id == "telegram-copy")
            .filter(SignalRecord.source == "telegram")
            .filter(SignalRecord.source_id == source.identifier)
            .all()
        )
    }

    parsed = needs_review = ignored = created = updated = 0
    class_counts: Counter[str] = Counter()
    stale_message_ids: list[str] = []

    for row in rows:
        text = (row.text or "").strip()
        has_media = bool((row.metadata_json or {}).get("grouped_id"))
        label = labels.get(row.id)

        if label and label.manual_class:
            try:
                message_class = MessageClass(label.manual_class)
            except ValueError:
                message_class = MessageClass.UNKNOWN
            classification_confidence = 1.0
            reasons = ["manual_label"]
            label.predicted_class = message_class.value
            label.predicted_confidence = classification_confidence
            label.reasons_json = reasons
        else:
            classification = classifier().classify(text, has_media=has_media)
            message_class = classification.message_class
            classification_confidence = classification.confidence
            reasons = classification.reasons
            if not label:
                label = MessageLabelRecord(
                    raw_event_id=row.id,
                    source_id=source.id,
                    predicted_class=message_class.value,
                    predicted_confidence=classification_confidence,
                    reasons_json=reasons,
                )
                db.add(label)
                labels[row.id] = label
            else:
                label.predicted_class = message_class.value
                label.predicted_confidence = classification_confidence
                label.reasons_json = reasons

        class_counts[message_class.value] += 1
        row.metadata_json = {
            **(row.metadata_json or {}),
            "classification": {
                "class": message_class.value,
                "confidence": classification_confidence,
                "reasons": reasons,
            },
        }

        # Only clean new recommendations enter the parser/backtest dataset.
        if message_class != MessageClass.NEW_SIGNAL:
            stale_message_ids.append(str(row.external_id))
            ignored += 1
            continue

        response = telegram_signal_parser.parse(
            text,
            timestamp=row.occurred_at,
            source_id=source.identifier,
            source_message_id=row.external_id,
            default_quote=str((source.config or {}).get("default_quote", "USDT")),
        )
        signal = response.signal
        if signal is None:
            stale_message_ids.append(str(row.external_id))
            ignored += 1
            continue

        blended_confidence = round(0.45 * classification_confidence + 0.55 * response.confidence, 2)
        signal.confidence = blended_confidence
        if signal.status == SignalStatus.PARSED and blended_confidence >= 0.78:
            parsed += 1
            signal.status = SignalStatus.PARSED
        else:
            needs_review += 1
            signal.status = SignalStatus.NEEDS_REVIEW

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
                "classification": message_class.value,
                "classification_confidence": classification_confidence,
                "edit_date": (row.metadata_json or {}).get("edit_date"),
                "historical_integrity": "edited" if (row.metadata_json or {}).get("edit_date") else "current_snapshot",
            },
        }
        existing = existing_signals.get(str(row.external_id))
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
            updated += 1
        else:
            new_row = SignalRecord(**values)
            db.add(new_row)
            existing_signals[str(row.external_id)] = new_row
            created += 1

    # Clean stale signals with a handful of DELETEs instead of one DELETE/message.
    for chunk in _chunks(list(dict.fromkeys(stale_message_ids))):
        db.query(SignalRecord).filter(
            SignalRecord.strategy_id == "telegram-copy",
            SignalRecord.source == "telegram",
            SignalRecord.source_id == source.identifier,
            SignalRecord.source_message_id.in_(chunk),
        ).delete(synchronize_session=False)

    db.commit()
    return {
        "examined": len(rows),
        "parsed": parsed,
        "needs_review": needs_review,
        "ignored": ignored,
        "created": created,
        "updated": updated,
        "classification": dict(class_counts),
    }


def parse_all_stored_messages(source_id: int) -> dict:
    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        return parse_stored_messages(db, source, limit=None)


def parse_stored_messages_for_source(source_id: int, limit: int | None = 1000) -> dict:
    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        return parse_stored_messages(db, source, limit=limit)
