from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.trading import PaperAccountRecord, PaperOrderRecord, PaperPositionRecord
from app.models.signal import SignalRecord
from app.services.paper_trading import ensure_default_account, paper_buy, refresh_positions, close_position

router=APIRouter(prefix="/paper", tags=["paper"])

class PaperBuy(BaseModel):
    symbol:str
    quote_amount:float=Field(gt=0)
    signal_id:int|None=None
    fee_bps:float=Field(default=10,ge=0,le=200)

@router.get("/account")
def account(db:Session=Depends(get_db)):
    a=ensure_default_account(db)
    return {"id":a.id,"name":a.name,"quote_currency":a.quote_currency,"initial_balance":a.initial_balance,"cash_balance":a.cash_balance,"equity":a.equity,"realized_pnl":a.realized_pnl}

@router.post("/reset")
def reset(db:Session=Depends(get_db)):
    a=ensure_default_account(db)
    db.query(PaperOrderRecord).filter(PaperOrderRecord.account_id==a.id).delete(synchronize_session=False)
    db.query(PaperPositionRecord).filter(PaperPositionRecord.account_id==a.id).delete(synchronize_session=False)
    a.cash_balance=a.initial_balance; a.equity=a.initial_balance; a.realized_pnl=0
    db.commit(); return {"ok":True}

@router.get("/positions")
def positions(db:Session=Depends(get_db)):
    a=ensure_default_account(db)
    rows=db.query(PaperPositionRecord).filter(PaperPositionRecord.account_id==a.id).order_by(PaperPositionRecord.id.desc()).all()
    return [{"id":p.id,"signal_id":p.signal_id,"symbol":p.symbol,"quantity":p.quantity,"entry_price":p.entry_price,"current_price":p.current_price,"unrealized_pnl":p.unrealized_pnl,"realized_pnl":p.realized_pnl,"status":p.status,"broker_id":p.broker_id,"opened_at":p.opened_at,"closed_at":p.closed_at} for p in rows]

@router.get("/orders")
def orders(db:Session=Depends(get_db)):
    a=ensure_default_account(db)
    rows=db.query(PaperOrderRecord).filter(PaperOrderRecord.account_id==a.id).order_by(PaperOrderRecord.id.desc()).limit(500).all()
    return [{"id":o.id,"symbol":o.symbol,"side":o.side,"quantity":o.quantity,"fill_price":o.fill_price,"fee":o.fee,"status":o.status,"broker_id":o.broker_id,"created_at":o.created_at} for o in rows]

@router.post("/buy")
async def buy(req:PaperBuy, db:Session=Depends(get_db)):
    try: return await paper_buy(db,ensure_default_account(db),symbol=req.symbol.upper(),quote_amount=req.quote_amount,signal_id=req.signal_id,fee_bps=req.fee_bps)
    except Exception as exc: raise HTTPException(400,str(exc)) from exc

@router.post("/refresh")
async def refresh(db:Session=Depends(get_db)):
    try: return await refresh_positions(db,ensure_default_account(db))
    except Exception as exc: raise HTTPException(400,str(exc)) from exc

@router.post("/positions/{position_id}/close")
async def close(position_id:int, db:Session=Depends(get_db)):
    try: return await close_position(db,ensure_default_account(db),position_id)
    except Exception as exc: raise HTTPException(400,str(exc)) from exc


@router.post("/signals/{signal_id}/execute")
async def execute_signal(signal_id:int, quote_amount:float=100.0, db:Session=Depends(get_db)):
    sig=db.get(SignalRecord,signal_id)
    if not sig: raise HTTPException(404,"Signal not found")
    if sig.side != "buy": raise HTTPException(400,"Paper spot V1 executes BUY signals only")
    try:
        return await paper_buy(db,ensure_default_account(db),symbol=sig.symbol,quote_amount=quote_amount,signal_id=sig.id,fee_bps=10.0)
    except Exception as exc: raise HTTPException(400,str(exc)) from exc
