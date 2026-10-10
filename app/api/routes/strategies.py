from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.strategy import StrategyConfigRecord

router=APIRouter(prefix="/strategies",tags=["strategies"])

DEFAULTS=[
 {"strategy_id":"telegram-copy","version":"1.1","enabled":True,"stage":"backtested","parameters":{"description":"Copy normalized Telegram spot signals"}},
 {"strategy_id":"news-event","version":"0.1","enabled":False,"stage":"draft","parameters":{"description":"News/event strategy plugin contract ready; add a NewsSource adapter"}},
 {"strategy_id":"technical-custom","version":"0.1","enabled":False,"stage":"draft","parameters":{"description":"Custom technical/optimization strategy plugin contract"}},
]

@router.get("")
def list_strategies(db:Session=Depends(get_db)):
    for d in DEFAULTS:
        if not db.query(StrategyConfigRecord).filter(StrategyConfigRecord.strategy_id==d["strategy_id"]).first(): db.add(StrategyConfigRecord(**d))
    db.commit()
    rows=db.query(StrategyConfigRecord).order_by(StrategyConfigRecord.id).all()
    return [{"id":r.id,"strategy_id":r.strategy_id,"version":r.version,"enabled":r.enabled,"stage":r.stage,"parameters":r.parameters_json} for r in rows]
