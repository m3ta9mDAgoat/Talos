from __future__ import annotations

from collections import Counter
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
    "original_unobserved": 0,
}


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
        for raw in raws:
            v=versions.get(raw.id)
            items.append({
                "raw_event_id":raw.id,"event_type":raw.event_type,"occurred_at":raw.occurred_at,"ingested_at":raw.ingested_at,
                "edit_date":(raw.metadata_json or {}).get("edit_date"),"delete_date":(raw.metadata_json or {}).get("delete_date"),
                "text":raw.text,"parsed":None if not v else {"revision_type":v.revision_type,"revision_at":v.revision_at,
                    "symbol":v.symbol,"side":v.side,"entry":v.entry_json,"stop":v.stop_json,"targets":v.targets_json,
                    "confidence":v.confidence,"status":v.status,"diff":v.diff_json,"integrity_flags":v.integrity_flags_json}
            })
        return {"source_id":source_id,"message_id":str(message_id),"versions":items}


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
        if len(versions)>1:
            recent.append({"message_id":mid,"revisions":len(versions),"latest_revision_at":versions[-1].revision_at,
                "flags":sorted(set(msg_flags)),"risk_points":points,"symbol":versions[-1].symbol})
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
