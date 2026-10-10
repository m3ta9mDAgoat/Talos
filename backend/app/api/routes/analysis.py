from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.models.analysis import AnalysisRunRecord, MessageLabelRecord, SignalBacktestRecord
from app.models.raw_event import RawEventRecord
from app.models.source import SourceRecord
from app.schemas.analysis import AnalysisRunRead, AnalysisTradeRead, AnalyzeSourceRequest, ManualLabelRequest, ReviewMessageRead
from app.services.channel_analysis import advance_run, create_run, replay_readiness

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _run_read(row: AnalysisRunRecord) -> AnalysisRunRead:
    return AnalysisRunRead(
        id=row.id, source_id=row.source_id, status=row.status, stage=row.stage,
        progress=row.progress, config=row.config_json or {}, summary=row.summary_json or {},
        error=row.error, created_at=row.created_at, started_at=row.started_at, completed_at=row.completed_at,
    )


@router.post("/sources/{source_id}/run", response_model=AnalysisRunRead)
async def analyze_source(source_id: int, request: AnalyzeSourceRequest, db: Session = Depends(get_db)) -> AnalysisRunRead:
    if not db.get(SourceRecord, source_id):
        raise HTTPException(status_code=404, detail="Source not found")
    row = create_run(source_id, request)
    return _run_read(row)


@router.post("/runs/{run_id}/advance", response_model=AnalysisRunRead)
async def advance_analysis(run_id: int, batch_size: int = Query(default=4, ge=1, le=12)) -> AnalysisRunRead:
    try:
        row = await advance_run(run_id, batch_size=batch_size)
        return _run_read(row)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        # Persist a useful error rather than leaving the UI apparently frozen.
        with SessionLocal() as db:  # type: ignore[name-defined]
            row = db.get(AnalysisRunRecord, run_id)
            if row:
                row.status = "error"; row.stage = "error"; row.error = f"{type(exc).__name__}: {exc}"
                db.commit(); db.refresh(row)
                return _run_read(row)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@router.get("/sources/{source_id}/readiness")
def source_replay_readiness(source_id: int, edit_policy: str = "from_edit", require_stop: bool = False):
    try:
        cfg = AnalyzeSourceRequest(edit_policy=edit_policy, require_stop=require_stop)
        return replay_readiness(source_id, cfg)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/sources/{source_id}/latest", response_model=AnalysisRunRead | None)
def latest_analysis(source_id: int, db: Session = Depends(get_db)):
    row = db.query(AnalysisRunRecord).filter(AnalysisRunRecord.source_id == source_id).order_by(AnalysisRunRecord.id.desc()).first()
    return _run_read(row) if row else None


@router.get("/runs/{run_id}", response_model=AnalysisRunRead)
def analysis_run(run_id: int, db: Session = Depends(get_db)) -> AnalysisRunRead:
    row = db.get(AnalysisRunRecord, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    return _run_read(row)


@router.get("/runs/{run_id}/trades", response_model=list[AnalysisTradeRead])
def analysis_trades(run_id: int, limit: int = Query(default=200, ge=1, le=1000), db: Session = Depends(get_db)) -> list[AnalysisTradeRead]:
    rows = db.query(SignalBacktestRecord).filter(SignalBacktestRecord.analysis_run_id == run_id).order_by(SignalBacktestRecord.timestamp.desc()).limit(limit).all()
    return [AnalysisTradeRead(id=r.id, signal_id=r.signal_id, timestamp=r.timestamp, symbol=r.symbol, exchange=r.exchange,
        market_symbol=r.market_symbol, status=r.status, return_pct=r.return_pct, result=r.result_json or {}) for r in rows]


@router.get("/sources/{source_id}/review", response_model=list[ReviewMessageRead])
def review_queue(source_id: int, limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)) -> list[ReviewMessageRead]:
    rows = (db.query(MessageLabelRecord, RawEventRecord).join(RawEventRecord, RawEventRecord.id == MessageLabelRecord.raw_event_id)
        .filter(MessageLabelRecord.source_id == source_id).filter(MessageLabelRecord.predicted_confidence < 0.80)
        .order_by(RawEventRecord.occurred_at.desc()).limit(limit).all())
    return [ReviewMessageRead(raw_event_id=raw.id, external_id=raw.external_id, occurred_at=raw.occurred_at, text=raw.text,
        predicted_class=label.predicted_class, confidence=label.predicted_confidence, manual_class=label.manual_class,
        reasons=label.reasons_json or []) for label, raw in rows]


@router.post("/messages/{raw_event_id}/label")
def label_message(raw_event_id: int, request: ManualLabelRequest, db: Session = Depends(get_db)) -> dict:
    row = db.query(MessageLabelRecord).filter(MessageLabelRecord.raw_event_id == raw_event_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Message classification not found")
    row.manual_class = request.message_class; db.commit()
    return {"ok": True, "raw_event_id": raw_event_id, "manual_class": request.message_class}
