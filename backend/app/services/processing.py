from __future__ import annotations

import hashlib
import threading
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.enums import MessageClass, SignalStatus
from app.db.session import SessionLocal
from app.models.analysis import MessageLabelRecord
from app.models.processing import ProcessingStateRecord
from app.models.raw_event import RawEventRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.models.telegram_audit import TelegramSignalVersionRecord
from app.services.message_classifier import classifier
from app.services.signal_parser import telegram_signal_parser

CLASSIFIER_VERSION = "rules-1.1"
PARSER_VERSION = "hybrid-telegram-3.0"
_PROCESS_LOCK = threading.RLock()


def _hash(row: RawEventRecord) -> str:
    raw = f"{row.external_id}|{row.text or ''}|{(row.metadata_json or {}).get('edit_date') or ''}|{row.event_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _revision_at(row: RawEventRecord) -> datetime:
    meta = row.metadata_json or {}
    value = meta.get("delete_date") or meta.get("edit_date")
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            pass
    return row.occurred_at


def _pending_rows(db: Session, source: SourceRecord, *, force: bool = False) -> list[RawEventRecord]:
    """Return only rows that actually need processing.

    Raw events are immutable revisions, so once a row has been processed with the
    current classifier/parser versions there is no value in loading it again on
    every catch-up cycle. Previous versions loaded every archived Telegram row
    into Python even when 100% were cached, which kept serverless Postgres hot and
    wasted transfer/compute.
    """
    query = db.query(RawEventRecord).filter(RawEventRecord.source_identifier == source.identifier)
    if not force:
        query = query.outerjoin(ProcessingStateRecord, ProcessingStateRecord.raw_event_id == RawEventRecord.id).filter(
            or_(
                ProcessingStateRecord.id.is_(None),
                ProcessingStateRecord.classifier_version != CLASSIFIER_VERSION,
                ProcessingStateRecord.parser_version != PARSER_VERSION,
                ProcessingStateRecord.status == "error",
            )
        )
    return query.order_by(RawEventRecord.id.asc()).all()


def _diff(prev: TelegramSignalVersionRecord | None, values: dict[str, Any], *, deleted: bool = False) -> tuple[dict[str, Any], list[str]]:
    if deleted:
        return {"deleted": True}, ["deleted_signal"]
    if prev is None:
        if values.get("revision_type") == "snapshot":
            return {"initial": True, "original_unobserved": True}, ["original_unobserved"]
        return {"initial": True}, []
    changes: dict[str, Any] = {}
    flags: list[str] = []
    mapping = {
        "symbol": (prev.symbol, values.get("symbol")),
        "side": (prev.side, values.get("side")),
        "entry": (prev.entry_json, values.get("entry")),
        "stop": (prev.stop_json, values.get("stop")),
        "targets": (prev.targets_json or [], values.get("targets") or []),
    }
    for key, (before, after) in mapping.items():
        if before != after:
            changes[key] = {"before": before, "after": after}
            flags.append(f"{key}_changed")
    old_text = " ".join((prev.raw_text or "").split())
    new_text = " ".join((values.get("raw_text") or "").split())
    if old_text != new_text:
        changes["text_changed"] = True
        if not flags:
            flags.append("text_only_change")
    return changes, flags


def _upsert_version(
    db: Session,
    *,
    row: RawEventRecord,
    source: SourceRecord,
    revision_type: str,
    parsed_values: dict[str, Any] | None,
    status: str,
) -> TelegramSignalVersionRecord:
    existing = db.query(TelegramSignalVersionRecord).filter(TelegramSignalVersionRecord.raw_event_id == row.id).first()
    prev = (
        db.query(TelegramSignalVersionRecord)
        .filter(
            TelegramSignalVersionRecord.source_id == source.id,
            TelegramSignalVersionRecord.source_message_id == str(row.external_id),
            TelegramSignalVersionRecord.raw_event_id != row.id,
        )
        .order_by(TelegramSignalVersionRecord.revision_at.desc(), TelegramSignalVersionRecord.id.desc())
        .first()
    )
    values = dict(parsed_values or {})
    values["revision_type"] = revision_type
    deleted = revision_type == "delete"
    # Include the actual revision text: omitting it mislabeled every edit as text-only.
    changes, flags = _diff(prev, {**values, "raw_text": row.text}, deleted=deleted)
    if prev and not deleted and not parsed_values and prev.status == "parsed":
        # A new unparseable revision is not proof that its old trading fields were erased.
        changes = {"unparsable_revision": True, "text_changed": " ".join((prev.raw_text or "").split()) != " ".join((row.text or "").split())}
        flags = ["signal_became_unparsable"]
    revision_at = _revision_at(row)
    payload = dict(
        raw_event_id=row.id,
        source_id=source.id,
        source_identifier=source.identifier,
        source_message_id=str(row.external_id),
        revision_type=revision_type,
        revision_at=revision_at,
        original_message_at=row.occurred_at,
        symbol=values.get("symbol"),
        side=values.get("side"),
        market_type=values.get("market_type"),
        confidence=values.get("confidence"),
        status=status,
        entry_json=values.get("entry"),
        stop_json=values.get("stop"),
        targets_json=values.get("targets") or [],
        raw_text=row.text,
        diff_json=changes,
        integrity_flags_json=flags,
    )
    if existing:
        for key, value in payload.items():
            setattr(existing, key, value)
        return existing
    existing = TelegramSignalVersionRecord(**payload)
    db.add(existing)
    return existing


def _process_one(db: Session, source: SourceRecord, row: RawEventRecord, *, force: bool = False, commit: bool = True) -> dict[str, Any]:
    digest = _hash(row)
    state = db.query(ProcessingStateRecord).filter(ProcessingStateRecord.raw_event_id == row.id).first()
    if (
        not force and state and state.content_hash == digest
        and state.classifier_version == CLASSIFIER_VERSION
        and state.parser_version == PARSER_VERSION
        and state.status in {"done", "ignored", "review", "deleted"}
    ):
        return {"cached": True, "raw_event_id": row.id}

    now = datetime.now(timezone.utc)
    metadata = dict(row.metadata_json or {})
    event_type = str(row.event_type or metadata.get("event_type") or "message")
    has_prior = db.query(RawEventRecord.id).filter(
        RawEventRecord.source_identifier == source.identifier,
        RawEventRecord.external_id == str(row.external_id),
        RawEventRecord.id != row.id,
    ).first() is not None
    revision_type = "delete" if event_type == "delete" else (
        "edit" if event_type == "edit" or (metadata.get("edit_date") and has_prior)
        else "snapshot" if metadata.get("edit_date")
        else "message"
    )
    existing_signal = db.query(SignalRecord).filter(
        SignalRecord.source == "telegram",
        SignalRecord.source_id == source.identifier,
        SignalRecord.source_message_id == str(row.external_id),
    ).first()

    if revision_type == "delete":
        if existing_signal:
            meta = dict(existing_signal.metadata_json or {})
            meta.update({"deleted": True, "deleted_at": _revision_at(row).isoformat(), "delete_raw_event_id": row.id})
            existing_signal.metadata_json = meta
        _upsert_version(db, row=row, source=source, revision_type="delete", parsed_values=None, status="deleted")
        if not state:
            state = ProcessingStateRecord(raw_event_id=row.id, source_id=source.id, content_hash=digest,
                classifier_version=CLASSIFIER_VERSION, parser_version=PARSER_VERSION, status="deleted", processed_at=now)
            db.add(state)
        else:
            state.content_hash=digest; state.status="deleted"; state.last_error=None; state.processed_at=now
        if commit:
            db.commit()
        else:
            db.flush()
        return {"cached": False, "deleted": True, "signal_id": int(existing_signal.id) if existing_signal else None, "raw_event_id": row.id, "revision_type": revision_type}

    text = (row.text or "").strip()
    has_media = bool(metadata.get("grouped_id"))
    label = db.query(MessageLabelRecord).filter(MessageLabelRecord.raw_event_id == row.id).first()
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
            pre_ai_signal = ai_parse_signal(text, timestamp=row.occurred_at, source_id=source.identifier,
                source_message_id=str(row.external_id), default_quote=str((source.config or {}).get("default_quote", "USDT")))
            if pre_ai_signal is not None:
                message_class = MessageClass.NEW_SIGNAL
                class_conf = max(0.75, pre_ai_signal.confidence)
                reasons = [*reasons, "ai_signal_classification"]
        except Exception:
            pre_ai_signal = None

    if not label:
        label = MessageLabelRecord(raw_event_id=row.id, source_id=source.id, predicted_class=message_class.value,
            predicted_confidence=class_conf, reasons_json=reasons)
        db.add(label)
    else:
        label.predicted_class=message_class.value; label.predicted_confidence=class_conf; label.reasons_json=reasons

    metadata["classification"] = {"class": message_class.value, "confidence": class_conf, "reasons": reasons}
    row.metadata_json = metadata

    should_parse = message_class == MessageClass.NEW_SIGNAL or (revision_type == "edit" and existing_signal is not None)
    signal_status = "ignored"
    signal_values: dict[str, Any] | None = None
    warnings: list[str] = []
    created = updated = False

    if should_parse:
        response = telegram_signal_parser.parse(text, timestamp=row.occurred_at, source_id=source.identifier,
            source_message_id=row.external_id, default_quote=str((source.config or {}).get("default_quote", "USDT")))
        signal = pre_ai_signal or response.signal
        warnings = list(response.warnings)
        if signal is None or response.confidence < 0.70:
            try:
                from app.services.ai_signal_parser import parse_signal as ai_parse_signal
                ai_signal = ai_parse_signal(text, timestamp=row.occurred_at, source_id=source.identifier,
                    source_message_id=str(row.external_id), default_quote=str((source.config or {}).get("default_quote", "USDT")))
                if ai_signal is not None:
                    signal = ai_signal; response.confidence=max(response.confidence, ai_signal.confidence); warnings.append("AI fallback parser used")
            except Exception:
                pass
        if signal is not None:
            blended = round(0.45 * class_conf + 0.55 * response.confidence, 4)
            signal.confidence = blended
            if signal.status == SignalStatus.PARSED and blended >= 0.78:
                signal.status = SignalStatus.PARSED; signal_status = "done"
            else:
                signal.status = SignalStatus.NEEDS_REVIEW; signal_status = "review"
            signal_values = dict(
                strategy_id=signal.strategy_id, strategy_version=signal.strategy_version,
                source=signal.source, source_id=source.identifier, source_message_id=str(row.external_id),
                timestamp=signal.timestamp, symbol=signal.symbol, side=signal.side.value,
                market_type=signal.market_type.value, confidence=signal.confidence, status=signal.status.value,
                entry=signal.entry.model_dump(mode="json") if signal.entry else None,
                stop=signal.stop.model_dump(mode="json") if signal.stop else None,
                targets=signal.targets, raw_text=signal.raw_text,
            )
            canonical_values = {**signal_values, "metadata_json": {**signal.metadata, "raw_event_id": row.id,
                "warnings": warnings, "classification": message_class.value, "classification_confidence": class_conf,
                "edit_date": metadata.get("edit_date"), "revision_type": revision_type, "deleted": False}}
            if existing_signal:
                for key, value in canonical_values.items():
                    setattr(existing_signal, key, value)
                updated = True
            else:
                existing_signal = SignalRecord(**canonical_values); db.add(existing_signal); db.flush(); created = True
            _upsert_version(db, row=row, source=source, revision_type=revision_type, parsed_values=signal_values, status=signal.status.value)
        else:
            # An edit that becomes unparsable must not erase the previously observed recommendation.
            _upsert_version(db, row=row, source=source, revision_type=revision_type, parsed_values=None, status="unparsed_edit" if revision_type == "edit" else "ignored")
    else:
        _upsert_version(db, row=row, source=source, revision_type=revision_type, parsed_values=None, status="ignored")

    if not state:
        state = ProcessingStateRecord(raw_event_id=row.id, source_id=source.id, content_hash=digest,
            classifier_version=CLASSIFIER_VERSION, parser_version=PARSER_VERSION, status=signal_status, processed_at=now)
        db.add(state)
    else:
        state.content_hash=digest; state.classifier_version=CLASSIFIER_VERSION; state.parser_version=PARSER_VERSION
        state.status=signal_status; state.last_error=None; state.processed_at=now
    if commit:
        db.commit()
    else:
        db.flush()
    return {"cached": False, "raw_event_id": row.id, "revision_type": revision_type,
        "classification": message_class.value, "signal_status": signal_status,
        "signal_id": int(existing_signal.id) if existing_signal else None, "created": created, "updated": updated}


def process_raw_event(source_id: int, raw_event_id: int, *, force: bool = False) -> dict[str, Any]:
    with _PROCESS_LOCK:
        with SessionLocal() as db:
            source = db.get(SourceRecord, source_id)
            row = db.get(RawEventRecord, raw_event_id)
            if not source or not row:
                raise ValueError("Source or raw event not found")
            return _process_one(db, source, row, force=force)


def process_source(source_id: int, *, force: bool = False, progress_callback: Callable[[int, int], None] | None = None) -> dict:
    with _PROCESS_LOCK:
        with SessionLocal() as db:
            source = db.get(SourceRecord, source_id)
            if not source:
                raise ValueError("Source not found")
            rows = _pending_rows(db, source, force=force)
            examined=processed=skipped_cached=created=updated=parsed=review=ignored=errors=deleted=0
            created_signal_ids: list[int]=[]; updated_signal_ids: list[int]=[]; classes: Counter[str]=Counter()
            total_rows = len(rows)
            if progress_callback:
                try: progress_callback(0, total_rows)
                except Exception: pass
            for row in rows:
                examined += 1
                try:
                    result = _process_one(db, source, row, force=force, commit=False)
                    if result.get("cached"):
                        skipped_cached += 1; continue
                    processed += 1
                    if result.get("deleted"): deleted += 1
                    if result.get("created") and result.get("signal_id"):
                        created += 1; created_signal_ids.append(int(result["signal_id"]))
                    if result.get("updated") and result.get("signal_id"):
                        updated += 1; updated_signal_ids.append(int(result["signal_id"]))
                    if result.get("signal_status") == "done": parsed += 1
                    elif result.get("signal_status") == "review": review += 1
                    elif not result.get("deleted"): ignored += 1
                    if result.get("classification"): classes[str(result["classification"])] += 1
                    if examined % 50 == 0:
                        db.commit()
                        if progress_callback:
                            try: progress_callback(examined, total_rows)
                            except Exception: pass
                except Exception as exc:
                    db.rollback(); errors += 1
                    state = db.query(ProcessingStateRecord).filter(ProcessingStateRecord.raw_event_id == row.id).first()
                    if not state:
                        state = ProcessingStateRecord(raw_event_id=row.id, source_id=source_id, content_hash=_hash(row),
                            classifier_version=CLASSIFIER_VERSION, parser_version=PARSER_VERSION, status="error",
                            last_error=f"{type(exc).__name__}: {exc}", processed_at=datetime.now(timezone.utc)); db.add(state)
                    else:
                        state.status="error"; state.last_error=f"{type(exc).__name__}: {exc}"; state.processed_at=datetime.now(timezone.utc)
                    db.commit()
            db.commit()
            if progress_callback:
                try: progress_callback(examined, total_rows)
                except Exception: pass
            source = db.get(SourceRecord, source_id)
            now = datetime.now(timezone.utc)
            source.config = {**(source.config or {}), "processing": {"classifier_version":CLASSIFIER_VERSION,
                "parser_version":PARSER_VERSION,"last_run_at":now.isoformat(),"messages_examined":examined}}
            db.commit()
            signal_total = db.query(SignalRecord).filter(SignalRecord.source_id == source.identifier).count()
            return {"source_id":source_id,"examined":examined,"processed":processed,"cached":skipped_cached,
                "pending":max(0,examined-skipped_cached-processed),"parsed":parsed,"needs_review":review,
                "ignored":ignored,"deleted":deleted,"errors":errors,"created":created,"updated":updated,
                "signals_total":signal_total,"classification":dict(classes),"classifier_version":CLASSIFIER_VERSION,
                "parser_version":PARSER_VERSION,"created_signal_ids":created_signal_ids,"updated_signal_ids":updated_signal_ids}


def source_processing_stats(db: Session, source: SourceRecord) -> dict:
    # Aggregate in Postgres instead of transferring every state/label row to the
    # web process each time the live Sources page refreshes.
    total_raw = db.query(func.count(RawEventRecord.id)).filter(RawEventRecord.source_identifier == source.identifier).scalar() or 0
    unique_messages = db.query(func.count(func.distinct(RawEventRecord.external_id))).filter(RawEventRecord.source_identifier == source.identifier).scalar() or 0
    state_groups = (
        db.query(ProcessingStateRecord.status, func.count(ProcessingStateRecord.id))
        .filter(ProcessingStateRecord.source_id == source.id)
        .group_by(ProcessingStateRecord.status)
        .all()
    )
    by_status = {str(status): int(count) for status, count in state_groups}
    processed_states = sum(by_status.values())
    signals = db.query(func.count(SignalRecord.id)).filter(SignalRecord.source_id == source.identifier).scalar() or 0
    class_expr = func.coalesce(MessageLabelRecord.manual_class, MessageLabelRecord.predicted_class)
    class_groups = (
        db.query(class_expr, func.count(MessageLabelRecord.id))
        .filter(MessageLabelRecord.source_id == source.id)
        .group_by(class_expr)
        .all()
    )
    by_class = {str(name): int(count) for name, count in class_groups if name is not None}
    versions = db.query(func.count(TelegramSignalVersionRecord.id)).filter(TelegramSignalVersionRecord.source_id == source.id).scalar() or 0
    return {"raw_rows":int(total_raw),"unique_messages":int(unique_messages),"processed_states":int(processed_states),
        "processing_status":by_status,"signals":int(signals),"signal_versions":int(versions),"classifications":by_class,
        "classifier_version":CLASSIFIER_VERSION,"parser_version":PARSER_VERSION}
