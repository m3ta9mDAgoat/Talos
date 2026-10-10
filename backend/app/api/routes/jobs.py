from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.jobs import JobRecord
from app.services.jobs import create_or_reuse_user_job, job_dict
from app.services.runtime_wake import wake_jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    kind: str
    payload: dict = Field(default_factory=dict)
    max_attempts: int = Field(default=20, ge=1, le=100)


@router.post("")
def new_job(req: JobCreate):
    allowed = {"source_import_full", "source_process", "source_sync", "telegram_replay", "strategy_backtest", "optimization", "research_extract"}
    if req.kind not in allowed: raise HTTPException(400, f"Unsupported job kind: {req.kind}")
    try:
        row, reused = create_or_reuse_user_job(req.kind, req.payload, max_attempts=req.max_attempts)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {**job_dict(row), "reused": reused}


@router.get("")
def list_jobs(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    return [job_dict(x) for x in db.query(JobRecord).order_by(JobRecord.id.desc()).limit(limit).all()]


@router.get("/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db)):
    row = db.get(JobRecord, job_id)
    if not row: raise HTTPException(404, "Job not found")
    return job_dict(row)


@router.post("/{job_id}/retry")
def retry(job_id: int, db: Session = Depends(get_db)):
    row = db.get(JobRecord, job_id)
    if not row: raise HTTPException(404, "Job not found")
    row.status = "queued"; row.attempts = 0; row.error = None
    from app.services.jobs import utcnow
    row.next_run_at = utcnow(); db.commit(); db.refresh(row)
    wake_jobs()
    return job_dict(row)


@router.post("/{job_id}/cancel")
def cancel(job_id: int, db: Session = Depends(get_db)):
    row = db.get(JobRecord, job_id)
    if not row: raise HTTPException(404, "Job not found")
    if row.status != "complete": row.status = "cancelled"
    db.commit(); db.refresh(row); return job_dict(row)
