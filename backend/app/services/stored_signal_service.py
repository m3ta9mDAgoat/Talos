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


def _latest_message_revisions(db: Session, source: SourceRecord, limit: int | None) -> list[RawEventRecord]:
    query = (
        db.query(RawEventRecord)
        .filter(RawEventRecord.source_identifier == source.identifier)
        .filter(RawEventRecord.text.isnot(None))
        .order_by(RawEventRecord.ingested_at.desc(), RawEventRecord.id.desc())
    )
    if limit is not None:
        query = query.limit(max(limit * 4, limit))
    rows = query.all()
    selected: OrderedDict[str, RawEventRecord] = OrderedDict()
    for row in rows:
        if row.external_id not in selected:
            selected[row.external_id] = row
        if limit is not None and len(selected) >= limit:
            break
    return list(selected.values())


def _upsert_label(db: Session, source: SourceRecord, row: RawEventRecord, predicted_class: str, confidence: float, reasons: list[str]) -> None:
    label = db.query(MessageLabelRecord).filter(MessageLabelRecord.raw_event_id == row.id).first()
    if not label:
        label = MessageLabelRecord(
            raw_event_id=row.id,
            source_id=source.id,
            predicted_class=predicted_class,
            predicted_confidence=confidence,
            reasons_json=reasons,
        )
        db.add(label)
    else:
        label.predicted_class = predicted_class
        label.predicted_confidence = confidence
        label.reasons_json = reasons


def parse_stored_messages(db: Session, source: SourceRecord, limit: int | None = 1000) -> dict:
    rows = _latest_message_revisions(db, source, limit)
    parsed = needs_review = ignored = created = updated = 0
    class_counts: Counter[str] = Counter()

    for row in rows:
        text = (row.text or "").strip()
        has_media = bool((row.metadata_json or {}).get("grouped_id"))
        classification = classifier().classify(text, has_media=has_media)
        class_counts[classification.message_class.value] += 1
        _upsert_label(
            db,
            source,
            row,
            classification.message_class.value,
            classification.confidence,
            classification.reasons,
        )
        row.metadata_json = {
            **(row.metadata_json or {}),
            "classification": {
                "class": classification.message_class.value,
                "confidence": classification.confidence,
                "reasons": classification.reasons,
            },
        }

        # Only signal-like messages enter the signal parser. This prevents results,
        # commentary, promotions and ordinary chat from contaminating backtests.
        if classification.message_class != MessageClass.NEW_SIGNAL:
            # Remove stale records created by an older/looser parser so a redeploy
            # immediately cleans the research dataset without re-importing Telegram.
            db.query(SignalRecord).filter(
                SignalRecord.strategy_id == "telegram-copy",
                SignalRecord.source == "telegram",
                SignalRecord.source_id == source.identifier,
                SignalRecord.source_message_id == row.external_id,
            ).delete(synchronize_session=False)
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
            db.query(SignalRecord).filter(
                SignalRecord.strategy_id == "telegram-copy",
                SignalRecord.source == "telegram",
                SignalRecord.source_id == source.identifier,
                SignalRecord.source_message_id == row.external_id,
            ).delete(synchronize_session=False)
            ignored += 1
            continue

        # Blend structural classifier confidence with parser completeness.
        blended_confidence = round(0.45 * classification.confidence + 0.55 * response.confidence, 2)
        signal.confidence = blended_confidence
        if signal.status == SignalStatus.PARSED and blended_confidence >= 0.78:
            parsed += 1
            signal.status = SignalStatus.PARSED
        else:
            needs_review += 1
            signal.status = SignalStatus.NEEDS_REVIEW

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
                "classification": classification.message_class.value,
                "classification_confidence": classification.confidence,
                "edit_date": (row.metadata_json or {}).get("edit_date"),
                "historical_integrity": "edited" if (row.metadata_json or {}).get("edit_date") else "current_snapshot",
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
        "classification": dict(class_counts),
    }


def parse_all_stored_messages(source_id: int) -> dict:
    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        return parse_stored_messages(db, source, limit=None)
