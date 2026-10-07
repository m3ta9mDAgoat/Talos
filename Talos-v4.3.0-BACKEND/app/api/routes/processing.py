from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.source import SourceRecord
from app.services.processing import process_source, source_processing_stats

router=APIRouter(prefix="/processing",tags=["processing"])

@router.get("/sources/{source_id}")
def stats(source_id:int, db:Session=Depends(get_db)):
    s=db.get(SourceRecord,source_id)
    if not s: raise HTTPException(404,"Source not found")
    return source_processing_stats(db,s)

@router.post("/sources/{source_id}/run")
def run(source_id:int, force:bool=False, db:Session=Depends(get_db)):
    if not db.get(SourceRecord,source_id): raise HTTPException(404,"Source not found")
    try:return process_source(source_id,force=force)
    except Exception as exc: raise HTTPException(400,str(exc)) from exc
