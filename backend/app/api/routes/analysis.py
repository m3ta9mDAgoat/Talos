from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.analysis import AnalysisRunRecord, MessageLabelRecord, SignalBacktestRecord
from app.models.raw_event import RawEventRecord
from app.models.source import SourceRecord
from app.schemas.analysis import (
    AnalysisRunRead,
    AnalysisTradeRead,
    AnalyzeSourceRequest,
    ManualLabelRequest,
    ReviewMessageRead,
)
from app.services.channel_analysis import create_run, start_run

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _run_read(row: AnalysisRunRecord) -> AnalysisRunRead:
    return AnalysisRunRead(
        id=row.id,
        source_id=row.source_id,
        status=row.status,
        stage=row.stage,
        progress=row.progress,
        config=row.config_json or {},
        summary=row.summary_json or {},
        error=row.error,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


@router.post("/sources/{source_id}/run", response_model=AnalysisRunRead)
async def analyze_source(source_id: int, request: AnalyzeSourceRequest, db: Session = Depends(get_db)) -> AnalysisRunRead:
    if not db.get(SourceRecord, source_id):
        raise HTTPException(status_code=404, detail="Source not found")
    row = create_run(source_id, request)
    start_run(row.id)
    return _run_read(row)


@router.get("/sources/{source_id}/latest", response_model=AnalysisRunRead | None)
def latest_analysis(source_id: int, db: Session = Depends(get_db)):
    row = (
        db.query(AnalysisRunRecord)
        .filter(AnalysisRunRecord.source_id == source_id)
        .order_by(AnalysisRunRecord.id.desc())
        .first()
    )
    return _run_read(row) if row else None


@router.get("/runs/{run_id}", response_model=AnalysisRunRead)
def analysis_run(run_id: int, db: Session = Depends(get_db)) -> AnalysisRunRead:
    row = db.get(AnalysisRunRecord, run_id)
    if not row:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    return _run_read(row)


@router.get("/runs/{run_id}/trades", response_model=list[AnalysisTradeRead])
def analysis_trades(
    run_id: int,
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[AnalysisTradeRead]:
    rows = (
        db.query(SignalBacktestRecord)
        .filter(SignalBacktestRecord.analysis_run_id == run_id)
        .order_by(SignalBacktestRecord.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        AnalysisTradeRead(
            id=r.id,
            signal_id=r.signal_id,
            timestamp=r.timestamp,
            symbol=r.symbol,
            exchange=r.exchange,
            market_symbol=r.market_symbol,
            status=r.status,
            return_pct=r.return_pct,
            result=r.result_json or {},
        )
        for r in rows
    ]


@router.get("/sources/{source_id}/review", response_model=list[ReviewMessageRead])
def review_queue(
    source_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[ReviewMessageRead]:
    rows = (
        db.query(MessageLabelRecord, RawEventRecord)
        .join(RawEventRecord, RawEventRecord.id == MessageLabelRecord.raw_event_id)
        .filter(MessageLabelRecord.source_id == source_id)
        .filter(MessageLabelRecord.predicted_confidence < 0.80)
        .order_by(RawEventRecord.occurred_at.desc())
        .limit(limit)
        .all()
    )
    return [
        ReviewMessageRead(
            raw_event_id=raw.id,
            external_id=raw.external_id,
            occurred_at=raw.occurred_at,
            text=raw.text,
            predicted_class=label.predicted_class,
            confidence=label.predicted_confidence,
            manual_class=label.manual_class,
            reasons=label.reasons_json or [],
        )
        for label, raw in rows
    ]


@router.post("/messages/{raw_event_id}/label")
def label_message(raw_event_id: int, request: ManualLabelRequest, db: Session = Depends(get_db)) -> dict:
    row = db.query(MessageLabelRecord).filter(MessageLabelRecord.raw_event_id == raw_event_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Message classification not found")
    row.manual_class = request.message_class
    db.commit()
    return {"ok": True, "raw_event_id": raw_event_id, "manual_class": request.message_class}
