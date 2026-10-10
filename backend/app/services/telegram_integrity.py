from __future__ import annotations

from collections import Counter
from datetime import timedelta, timezone
from difflib import SequenceMatcher
import re
from typing import Any

from app.db.session import SessionLocal
from app.models.raw_event import RawEventRecord
from app.models.source import SourceRecord
from app.models.telegram_audit import TelegramSignalVersionRecord


RISK_POINTS = {
    "symbol_changed": 30,
    "side_changed": 30,
    "entry_changed": 18,
    "deleted_signal": 18,
    "stop_changed": 7,
    "targets_changed": 4,
    "text_only_change": 0,
    "signal_became_unparsable": 12,
    "original_unobserved": 0,
}


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"[\w]+", (text or "").casefold(), flags=re.UNICODE))


def classify_revision(revision_type: str, flags: list[str], has_prior_original: bool) -> dict[str, Any]:
    """Categorise an observed edit, never infer motive or authorship."""
    if revision_type == "snapshot" and not has_prior_original:
        return {"kind": "original_not_observed", "severity": "unknown", "explanation": "Archived edited snapshot; original content was not observed. No attribution conclusion is possible."}
    if revision_type == "delete":
        return {"kind": "deleted", "severity": "review", "explanation": "The channel deleted this message; reason unknown."}
    if revision_type == "message":
        return {"kind": "initial_observation", "severity": "info", "explanation": "Initial message revision observed."}
    changes = set(flags or [])
    if changes & {"symbol_changed", "side_changed", "entry_changed"}:
        return {"kind": "material_signal_edit", "severity": "high", "explanation": "A trading-critical field changed after publication; compare both versions and market timing."}
    if "signal_became_unparsable" in changes:
        return {"kind": "signal_unparsable_after_edit", "severity": "review", "explanation": "An edit could not be parsed as a signal; the preserved original should be inspected."}
    if changes & {"stop_changed", "targets_changed"}:
        return {"kind": "risk_or_target_edit", "severity": "review", "explanation": "Stop/target levels changed; assess edits separately from original performance."}
    if "text_only_change" in changes:
        return {"kind": "text_or_context_edit", "severity": "info", "explanation": "Text changed but extracted trading fields stayed the same; it may be formatting/context."}
    return {"kind": "no_detected_trading_change", "severity": "info", "explanation": "No parsed trading field change detected."}


def earlier_cross_source_matches(db, raw: RawEventRecord, *, limit: int = 3) -> list[dict[str, Any]]:
    """Find prior *observed* substantial textual overlap, NOT plagiarism proof.

    A short/generic BUY template is too common to infer attribution. A match must
    be both long and distinctive and be observed BEFORE the inspected revision.
    """
    normalized = _normalized(raw.text or "")
    tokens = set(normalized.split())
    if len(normalized) < 85 or len(tokens) < 12:
        return []
    observed = _utc(raw.ingested_at)
    if observed is None:
        return []
    since = observed - timedelta(days=7)
    candidates = (db.query(RawEventRecord)
        .filter(RawEventRecord.source_type == "telegram",
                RawEventRecord.source_identifier != raw.source_identifier,
                RawEventRecord.ingested_at < raw.ingested_at,
                RawEventRecord.ingested_at >= since,
                RawEventRecord.text.isnot(None))
        .order_by(RawEventRecord.ingested_at.desc()).limit(1500).all())
    matches = []
    for other in candidates:
        prior_text = _normalized(other.text or "")
        other_tokens = set(prior_text.split())
        if len(prior_text) < 85 or len(other_tokens) < 12:
            continue
        overlap = len(tokens & other_tokens)/max(1,len(tokens | other_tokens))
        if overlap < .72:
            continue
        similarity = SequenceMatcher(None, normalized[:5000], prior_text[:5000], autojunk=False).ratio()
        if similarity < .88:
            continue
        other_seen = _utc(other.ingested_at)
        matches.append({
            "source": other.source_identifier,
            "message_id": other.external_id,
            "published_at": other.occurred_at.isoformat() if other.occurred_at else None,
            "first_observed_at": other_seen.isoformat() if other_seen else None,
            "similarity": round(similarity, 4),
            "shared_token_ratio": round(overlap, 4),
            "basis": "Earlier matching text observed in another monitored Telegram source; not proof of plagiarism, authorship, or permission.",
        })
    return sorted(matches, key=lambda x: (-x["similarity"], x["first_observed_at"]))[:limit]


def message_history(source_id: int, message_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        raws = (
            db.query(RawEventRecord)
            .filter(RawEventRecord.source_identifier == source.identifier, RawEventRecord.external_id == str(message_id))
            .order_by(RawEventRecord.ingested_at.asc(), RawEventRecord.id.asc())
            .all()
        )
        versions = {
            r.raw_event_id: r
            for r in db.query(TelegramSignalVersionRecord)
            .filter(TelegramSignalVersionRecord.source_id == source_id, TelegramSignalVersionRecord.source_message_id == str(message_id))
            .all()
        }
        items=[]
        original_seen = any(v and v.revision_type == "message" for v in versions.values())
        for raw in raws:
            v=versions.get(raw.id)
            flags = list(v.integrity_flags_json or []) if v else []
            kind = str(v.revision_type) if v else str(raw.event_type or "message")
            items.append({
                "assessment": classify_revision(kind, flags, original_seen),
                "forward_origin": (raw.metadata_json or {}).get("forward_origin"),
                "forward_author": (raw.metadata_json or {}).get("forward_author"),
                "forward_channel_post": (raw.metadata_json or {}).get("forward_channel_post"),
                "earlier_matching_sources": earlier_cross_source_matches(db, raw),
                "raw_event_id":raw.id,"event_type":raw.event_type,"occurred_at":raw.occurred_at,"ingested_at":raw.ingested_at,
                "edit_date":(raw.metadata_json or {}).get("edit_date"),"delete_date":(raw.metadata_json or {}).get("delete_date"),
                "text":raw.text,"parsed":None if not v else {"revision_type":v.revision_type,"revision_at":v.revision_at,
                    "symbol":v.symbol,"side":v.side,"entry":v.entry_json,"stop":v.stop_json,"targets":v.targets_json,
                    "confidence":v.confidence,"status":v.status,"diff":v.diff_json,"integrity_flags":v.integrity_flags_json}
            })
        return {"source_id":source_id,"message_id":str(message_id),"versions":items,
            "provenance_notice": "Observed edits or textual overlap cannot establish plagiarism. Only recorded revisions and earlier observed matches are evidence. Historical snapshots cannot reconstruct overwritten originals."}


def integrity_summary(source_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        source=db.get(SourceRecord,source_id)
        if not source: raise ValueError("Source not found")
        rows=(db.query(TelegramSignalVersionRecord)
              .filter(TelegramSignalVersionRecord.source_id==source_id)
              .order_by(TelegramSignalVersionRecord.source_message_id.asc(),TelegramSignalVersionRecord.revision_at.asc(),TelegramSignalVersionRecord.id.asc()).all())
    grouped:dict[str,list[TelegramSignalVersionRecord]]={}
    for r in rows: grouped.setdefault(r.source_message_id,[]).append(r)
    observed=edited=deleted=snapshots_without_original=0; flags=Counter(); risk_total=0; risky_messages=0
    recent=[]
    for mid,versions in grouped.items():
        if not versions: continue
        observed+=1
        if len(versions)>1 or any(v.revision_type=="edit" for v in versions): edited+=1
        if any(v.revision_type=="delete" for v in versions): deleted+=1
        if versions and versions[0].revision_type == "snapshot": snapshots_without_original += 1
        msg_flags=[]
        for v in versions[1:] if len(versions)>1 else []:
            for f in (v.integrity_flags_json or []):
                flags[f]+=1; msg_flags.append(f)
        points=min(100,sum(RISK_POINTS.get(f,2) for f in set(msg_flags)))
        risk_total+=points
        if points>0: risky_messages+=1
        if len(versions)>1 or versions[0].revision_type in ("snapshot", "edit"):
            latest = versions[-1]
            recent.append({"message_id":mid,"revisions":len(versions),"latest_revision_at":latest.revision_at,
                "flags":sorted(set(msg_flags)),"risk_points":points,"symbol":latest.symbol,
                "assessment": classify_revision(latest.revision_type, list(latest.integrity_flags_json or []),
                    any(v.revision_type == "message" for v in versions))})
    score=100.0 if not observed else max(0.0,100.0-(risk_total/observed))
    # This is an audit-quality indicator, never an accusation of intent.
    if observed < 25: confidence="insufficient"
    elif observed < 100: confidence="low"
    elif observed < 300: confidence="medium"
    else: confidence="high"
    return {"source_id":source_id,"observed_messages":observed,"edited_messages":edited,"deleted_messages":deleted,
        "risky_messages":risky_messages,"snapshots_without_original":snapshots_without_original,
        "integrity_score":round(score,4),"audit_confidence":confidence,
        "flag_counts":dict(flags),"recent_edits":sorted(recent,key=lambda x:x["latest_revision_at"],reverse=True)[:100],
        "note":"Integrity score measures observed revision risk patterns. It does not infer intent or dishonesty."}
