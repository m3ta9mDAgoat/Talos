from __future__ import annotations

import hashlib
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.core.enums import MessageClass, SignalStatus
from app.db.session import SessionLocal
from app.models.analysis import MessageLabelRecord
from app.models.processing import ProcessingStateRecord
from app.models.raw_event import RawEventRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.services.message_classifier import classifier
from app.services.signal_parser import telegram_signal_parser

CLASSIFIER_VERSION = "rules-1.1"
PARSER_VERSION = "hybrid-telegram-3.0"


def _hash(row: RawEventRecord) -> str:
    raw = f"{row.external_id}|{row.text or ''}|{(row.metadata_json or {}).get('edit_date') or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _latest_rows(db: Session, source: SourceRecord) -> list[RawEventRecord]:
    rows = (
        db.query(RawEventRecord)
        .filter(RawEventRecord.source_identifier == source.identifier)
        .filter(RawEventRecord.text.isnot(None))
        .order_by(RawEventRecord.ingested_at.desc(), RawEventRecord.id.desc())
        .all()
    )
    selected: OrderedDict[str, RawEventRecord] = OrderedDict()
    for row in rows:
        selected.setdefault(str(row.external_id), row)
    return list(selected.values())


def process_source(source_id: int, *, force: bool = False) -> dict:
    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        rows = _latest_rows(db, source)
        states = {
            s.raw_event_id: s
            for s in db.query(ProcessingStateRecord).filter(ProcessingStateRecord.source_id == source_id).all()
        }
        labels_by_raw = {
            r.raw_event_id: r
            for r in db.query(MessageLabelRecord).filter(MessageLabelRecord.source_id == source_id).all()
        }
        signals_by_message = {
            str(s.source_message_id): s
            for s in db.query(SignalRecord)
            .filter(SignalRecord.source == "telegram")
            .filter(SignalRecord.source_id == source.identifier)
            .all()
        }

        examined = processed = skipped_cached = created = updated = parsed = review = ignored = errors = 0
        classes: Counter[str] = Counter()
        now = datetime.now(timezone.utc)

        for row in rows:
            examined += 1
            digest = _hash(row)
            state = states.get(row.id)
            if (
                not force and state and state.content_hash == digest
                and state.classifier_version == CLASSIFIER_VERSION
                and state.parser_version == PARSER_VERSION
                and state.status in {"done", "ignored", "review"}
            ):
                skipped_cached += 1
                continue

            try:
                text = (row.text or "").strip()
                has_media = bool((row.metadata_json or {}).get("grouped_id"))
                label = labels_by_raw.get(row.id)
                if label and label.manual_class:
                    try:
                        message_class = MessageClass(label.manual_class)
                    except ValueError:
                        message_class = MessageClass.UNKNOWN
                    class_conf = 1.0
                    reasons = ["manual_label"]
                else:
                    c = classifier().classify(text, has_media=has_media)
                    message_class, class_conf, reasons = c.message_class, c.confidence, c.reasons

                pre_ai_signal = None
                if message_class == MessageClass.UNKNOWN:
                    try:
                        from app.services.ai_signal_parser import parse_signal as ai_parse_signal
                        pre_ai_signal = ai_parse_signal(text, timestamp=row.occurred_at, source_id=source.identifier, source_message_id=str(row.external_id), default_quote=str((source.config or {}).get("default_quote", "USDT")))
                        if pre_ai_signal is not None:
                            message_class = MessageClass.NEW_SIGNAL
                            class_conf = max(0.75, pre_ai_signal.confidence)
                            reasons = [*reasons, "ai_signal_classification"]
                    except Exception:
                        pre_ai_signal = None

                if not label:
                    label = MessageLabelRecord(
                        raw_event_id=row.id, source_id=source_id,
                        predicted_class=message_class.value,
                        predicted_confidence=class_conf, reasons_json=reasons,
                    )
                    db.add(label); labels_by_raw[row.id] = label
                else:
                    label.predicted_class = message_class.value
                    label.predicted_confidence = class_conf
                    label.reasons_json = reasons

                classes[message_class.value] += 1
                row.metadata_json = {
                    **(row.metadata_json or {}),
                    "classification": {"class": message_class.value, "confidence": class_conf, "reasons": reasons},
                }

                signal_status = "ignored"
                existing = signals_by_message.get(str(row.external_id))
                if message_class == MessageClass.NEW_SIGNAL:
                    response = telegram_signal_parser.parse(
                        text, timestamp=row.occurred_at, source_id=source.identifier,
                        source_message_id=row.external_id,
                        default_quote=str((source.config or {}).get("default_quote", "USDT")),
                    )
                    signal = pre_ai_signal or response.signal
                    if pre_ai_signal is not None:
                        response.confidence = max(response.confidence, pre_ai_signal.confidence)
                        response.warnings.append("AI fallback classifier/parser used")
                    if signal is None or response.confidence < 0.70:
                        try:
                            from app.services.ai_signal_parser import parse_signal as ai_parse_signal
                            ai_signal = ai_parse_signal(text, timestamp=row.occurred_at, source_id=source.identifier, source_message_id=str(row.external_id), default_quote=str((source.config or {}).get("default_quote", "USDT")))
                            if ai_signal is not None:
                                signal = ai_signal
                                response.confidence = max(response.confidence, ai_signal.confidence)
                                response.warnings.append("AI fallback parser used")
                        except Exception:
                            pass
                    if signal is not None:
                        blended = round(0.45 * class_conf + 0.55 * response.confidence, 4)
                        signal.confidence = blended
                        if signal.status == SignalStatus.PARSED and blended >= 0.78:
                            signal.status = SignalStatus.PARSED; parsed += 1; signal_status = "done"
                        else:
                            signal.status = SignalStatus.NEEDS_REVIEW; review += 1; signal_status = "review"
                        values = dict(
                            strategy_id=signal.strategy_id, strategy_version=signal.strategy_version,
                            source=signal.source, source_id=source.identifier,
                            source_message_id=str(row.external_id), timestamp=signal.timestamp,
                            symbol=signal.symbol, side=signal.side.value, market_type=signal.market_type.value,
                            confidence=signal.confidence, status=signal.status.value,
                            entry=signal.entry.model_dump(mode="json") if signal.entry else None,
                            stop=signal.stop.model_dump(mode="json") if signal.stop else None,
                            targets=signal.targets, raw_text=signal.raw_text,
                            metadata_json={**signal.metadata, "raw_event_id": row.id,
                                "warnings": response.warnings, "classification": message_class.value,
                                "classification_confidence": class_conf,
                                "edit_date": (row.metadata_json or {}).get("edit_date")},
                        )
                        if existing:
                            for k,v in values.items(): setattr(existing,k,v)
                            updated += 1
                        else:
                            existing = SignalRecord(**values); db.add(existing)
                            signals_by_message[str(row.external_id)] = existing; created += 1
                    else:
                        ignored += 1
                        if existing: db.delete(existing); signals_by_message.pop(str(row.external_id), None)
                else:
                    ignored += 1
                    if existing:
                        db.delete(existing); signals_by_message.pop(str(row.external_id), None)

                if not state:
                    state = ProcessingStateRecord(
                        raw_event_id=row.id, source_id=source_id, content_hash=digest,
                        classifier_version=CLASSIFIER_VERSION, parser_version=PARSER_VERSION,
                        status=signal_status, processed_at=now,
                    )
                    db.add(state); states[row.id] = state
                else:
                    state.content_hash=digest; state.classifier_version=CLASSIFIER_VERSION
                    state.parser_version=PARSER_VERSION; state.status=signal_status
                    state.last_error=None; state.processed_at=now
                processed += 1
            except Exception as exc:
                errors += 1
                if not state:
                    state = ProcessingStateRecord(raw_event_id=row.id, source_id=source_id, content_hash=digest,
                        classifier_version=CLASSIFIER_VERSION, parser_version=PARSER_VERSION,
                        status="error", last_error=str(exc), processed_at=now)
                    db.add(state)
                else:
                    state.status="error"; state.last_error=str(exc); state.processed_at=now

        source.config = {**(source.config or {}), "processing": {
            "classifier_version": CLASSIFIER_VERSION, "parser_version": PARSER_VERSION,
            "last_run_at": now.isoformat(), "messages_examined": examined,
        }}
        db.commit()
        signal_total = db.query(SignalRecord).filter(SignalRecord.source_id == source.identifier).count()
        pending = max(0, examined - skipped_cached - processed)
        return {
            "source_id": source_id, "examined": examined, "processed": processed,
            "cached": skipped_cached, "pending": pending, "parsed": parsed,
            "needs_review": review, "ignored": ignored, "errors": errors,
            "created": created, "updated": updated, "signals_total": signal_total,
            "classification": dict(classes), "classifier_version": CLASSIFIER_VERSION,
            "parser_version": PARSER_VERSION,
        }


def source_processing_stats(db: Session, source: SourceRecord) -> dict:
    total_raw = db.query(RawEventRecord).filter(RawEventRecord.source_identifier == source.identifier).count()
    unique_messages = db.query(RawEventRecord.external_id).filter(RawEventRecord.source_identifier == source.identifier).distinct().count()
    states = db.query(ProcessingStateRecord).filter(ProcessingStateRecord.source_id == source.id).all()
    by_status = Counter(s.status for s in states)
    signals = db.query(SignalRecord).filter(SignalRecord.source_id == source.identifier).count()
    labels = db.query(MessageLabelRecord).filter(MessageLabelRecord.source_id == source.id).all()
    by_class = Counter((x.manual_class or x.predicted_class) for x in labels)
    return {
        "raw_rows": total_raw, "unique_messages": unique_messages,
        "processed_states": len(states), "processing_status": dict(by_status),
        "signals": signals, "classifications": dict(by_class),
        "classifier_version": CLASSIFIER_VERSION, "parser_version": PARSER_VERSION,
    }
