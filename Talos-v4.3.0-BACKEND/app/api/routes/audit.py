from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.trading import ExecutionAuditRecord
router=APIRouter(prefix="/audit",tags=["audit"])
@router.get("")
def audit(limit:int=Query(default=200,ge=1,le=1000),db:Session=Depends(get_db)):
    rows=db.query(ExecutionAuditRecord).order_by(ExecutionAuditRecord.id.desc()).limit(limit).all()
    return [{"id":r.id,"mode":r.mode,"broker_id":r.broker_id,"symbol":r.symbol,"action":r.action,"status":r.status,"error":r.error,"created_at":r.created_at,"request":r.request_json,"response":r.response_json} for r in rows]
