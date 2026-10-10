from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.trading import PaperAccountRecord, PaperOrderRecord, PaperPositionRecord
from app.services.brokers import best_venue


def ensure_default_account(db: Session) -> PaperAccountRecord:
    row = db.query(PaperAccountRecord).filter(PaperAccountRecord.name == "Default Paper").first()
    if not row:
        row = PaperAccountRecord(name="Default Paper", initial_balance=10000.0, cash_balance=10000.0, equity=10000.0)
        db.add(row); db.commit(); db.refresh(row)
    return row


async def paper_buy(db: Session, account: PaperAccountRecord, *, symbol: str, quote_amount: float, signal_id: int|None=None, fee_bps: float=10.0) -> dict:
    venue = await best_venue(symbol)
    best = venue.get("best")
    if not best or not best.get("last"): raise ValueError(f"No live public ticker found for {symbol}")
    price = float(best["ask"] or best["last"])
    fee = quote_amount * fee_bps / 10000.0
    total = quote_amount + fee
    if total > account.cash_balance: raise ValueError("Insufficient paper cash")
    qty = quote_amount / price
    account.cash_balance -= total
    order = PaperOrderRecord(account_id=account.id, signal_id=signal_id, symbol=symbol, side="buy", quantity=qty,
                             fill_price=price, fee=fee, broker_id=best["broker_id"], status="filled")
    pos = PaperPositionRecord(account_id=account.id, signal_id=signal_id, symbol=symbol, quantity=qty,
                              entry_price=price, current_price=price, broker_id=best["broker_id"], status="open")
    db.add_all([order,pos]); db.commit(); db.refresh(order); db.refresh(pos)
    return {"order_id": order.id, "position_id": pos.id, "symbol":symbol,"quantity":qty,"fill_price":price,"fee":fee,"broker":best["broker_id"]}


async def refresh_positions(db: Session, account: PaperAccountRecord) -> dict:
    positions = db.query(PaperPositionRecord).filter(PaperPositionRecord.account_id==account.id, PaperPositionRecord.status=="open").all()
    unreal = 0.0
    for p in positions:
        venue = await best_venue(p.symbol, [p.broker_id] if p.broker_id else None)
        best = venue.get("best")
        if best and best.get("last"):
            p.current_price = float(best["last"])
            p.unrealized_pnl = (p.current_price-p.entry_price)*p.quantity
            unreal += p.unrealized_pnl
    account.equity = account.cash_balance + sum(p.current_price*p.quantity for p in positions)
    account.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"account_id":account.id,"cash":account.cash_balance,"equity":account.equity,"realized_pnl":account.realized_pnl,"unrealized_pnl":unreal,"open_positions":len(positions)}


async def close_position(db: Session, account: PaperAccountRecord, position_id: int, fee_bps: float=10.0) -> dict:
    p = db.get(PaperPositionRecord, position_id)
    if not p or p.account_id != account.id or p.status != "open": raise ValueError("Open paper position not found")
    venue = await best_venue(p.symbol, [p.broker_id] if p.broker_id else None)
    best = venue.get("best")
    if not best: raise ValueError("Ticker unavailable")
    price=float(best["bid"] or best["last"]); gross=price*p.quantity; fee=gross*fee_bps/10000.0
    pnl=(price-p.entry_price)*p.quantity-fee
    account.cash_balance += gross-fee; account.realized_pnl += pnl
    p.current_price=price; p.realized_pnl=pnl; p.unrealized_pnl=0; p.status="closed"; p.closed_at=datetime.now(timezone.utc)
    order=PaperOrderRecord(account_id=account.id, signal_id=p.signal_id, symbol=p.symbol, side="sell", quantity=p.quantity, fill_price=price, fee=fee, broker_id=best["broker_id"], status="filled")
    db.add(order); db.commit()
    await refresh_positions(db, account)
    return {"position_id":p.id,"fill_price":price,"realized_pnl":pnl,"fee":fee}
