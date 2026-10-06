from __future__ import annotations
import asyncio
from fastapi import APIRouter, HTTPException, Query
from app.services.brokers import SUPPORTED, best_venue, private_status, public_status, ticker

router=APIRouter(prefix="/exchanges", tags=["exchanges"])

@router.get("")
async def exchanges():
    return await asyncio.gather(*(public_status(x) for x in SUPPORTED))

@router.get("/connections")
async def connections():
    return await asyncio.gather(*(private_status(x) for x in SUPPORTED))

@router.get("/{broker_id}/ticker")
async def get_ticker(broker_id:str, symbol:str=Query(...)):
    if broker_id not in SUPPORTED: raise HTTPException(404,"Unsupported exchange")
    try: return await ticker(broker_id,symbol.upper())
    except Exception as exc: raise HTTPException(400,str(exc)) from exc

@router.get("/best/venue")
async def get_best_venue(symbol:str=Query(...)):
    try: return await best_venue(symbol.upper())
    except Exception as exc: raise HTTPException(400,str(exc)) from exc
