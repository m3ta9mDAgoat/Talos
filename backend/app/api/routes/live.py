from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.session import get_db
from app.models.trading import ExecutionAuditRecord
from app.services.brokers import SUPPORTED, place_live_order

router=APIRouter(prefix="/live", tags=["live"])
class LiveOrder(BaseModel):
    broker_id:str
    symbol:str
    side:str
    amount:float=Field(gt=0)
    order_type:str="market"
    price:float|None=None
    confirm_live:bool=False

@router.get("/status")
def status(): return {"enabled":settings.live_trading_enabled,"guard":"confirm_live must be true; withdrawal permissions are never required"}

@router.post("/order")
async def order(req:LiveOrder, db:Session=Depends(get_db)):
    if req.broker_id not in SUPPORTED: raise HTTPException(400,"Unsupported exchange")
    if req.side not in {"buy","sell"}: raise HTTPException(400,"side must be buy/sell")
    if not req.confirm_live: raise HTTPException(400,"confirm_live=true is required for a real order")
    audit=ExecutionAuditRecord(mode="live",broker_id=req.broker_id,symbol=req.symbol,action="create_order",status="started",request_json=req.model_dump())
    db.add(audit);db.commit();db.refresh(audit)
    try:
        result=await place_live_order(broker_id=req.broker_id,symbol=req.symbol.upper(),side=req.side,amount=req.amount,order_type=req.order_type,price=req.price)
        audit.status="success"; audit.response_json=result; db.commit(); return {"audit_id":audit.id,"order":result}
    except Exception as exc:
        audit.status="error";audit.error=str(exc);db.commit();raise HTTPException(400,str(exc)) from exc
