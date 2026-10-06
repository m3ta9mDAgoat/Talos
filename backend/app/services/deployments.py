from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from app.db.session import SessionLocal
from app.models.raw_event import RawEventRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.models.strategy import StrategyConfigRecord
from app.models.research import StrategyDeploymentRecord
from app.services.market_data import market_data_resolver
from app.services.strategy_engine import entry_ok
from app.services.paper_trading import ensure_default_account, paper_buy
from app.services.brokers import SUPPORTED, best_venue, credentials_present, place_live_order


def now():return datetime.now(timezone.utc)


async def _technical_trigger(definition:dict[str,Any])->tuple[bool,str|None,str|None]:
    symbol=(definition.get('symbols') or ['BTC/USDT'])[0]; tf=definition.get('timeframe','1h'); end=now(); start=end-timedelta(days=30)
    markets=await market_data_resolver.resolve(symbol,SUPPORTED)
    for m in markets:
        cs=await market_data_resolver.candles(exchange_id=m.exchange,symbol=m.symbol,timeframe=tf,start=start,end=end,max_bars=2000)
        if len(cs)>3:
            key=cs[-2].timestamp.isoformat();return entry_ok(cs,len(cs)-2,definition),symbol,key
    return False,symbol,None


async def tick(deployment_id:int,confirm_live:bool=False)->dict[str,Any]:
    with SessionLocal() as db:
        dep=db.get(StrategyDeploymentRecord,deployment_id)
        if not dep or not dep.enabled:raise ValueError('Enabled deployment not found')
        strat=db.get(StrategyConfigRecord,dep.strategy_config_id)
        if not strat:raise ValueError('Strategy not found')
        params=dict(strat.parameters_json or {});kind=params.get('kind','signal_copy');definition=dict(params.get('definition') or {});state=dict(dep.state_json or {});cfg=dict(dep.config_json or {})
    symbol=None;signal_id=None;trigger=False
    if kind=='signal_copy':
        source_id=int(definition.get('source_id') or 0)
        with SessionLocal() as db:
            source=db.get(SourceRecord,source_id)
            if not source:raise ValueError('Source missing')
            last=int(state.get('signal_id',0));row=db.query(SignalRecord).filter(SignalRecord.source_id==source.identifier,SignalRecord.id>last,SignalRecord.status=='parsed',SignalRecord.side=='buy').order_by(SignalRecord.id.asc()).first()
            if row:signal_id=row.id;symbol=row.symbol;trigger=True;state['signal_id']=row.id
    elif kind=='source_event':
        source_id=int(definition.get('source_id') or 0)
        with SessionLocal() as db:
            source=db.get(SourceRecord,source_id)
            if not source:raise ValueError('Source missing')
            last=int(state.get('raw_id',0));row=db.query(RawEventRecord).filter(RawEventRecord.source_identifier==source.identifier,RawEventRecord.id>last).order_by(RawEventRecord.id.asc()).first()
            if row:
                state['raw_id']=row.id;text=(row.text or '').lower();anyk=[x.lower() for x in definition.get('keywords_any') or []];none=[x.lower() for x in definition.get('keywords_none') or []]
                trigger=(not anyk or any(k in text for k in anyk)) and not any(k in text for k in none);symbol=(definition.get('symbols') or ['BTC/USDT'])[0]
    else:
        trigger,symbol,key=await _technical_trigger(definition)
        if key and state.get('technical_candle')==key: trigger=False
        if key: state['technical_candle']=key
    with SessionLocal() as db:
        dep=db.get(StrategyDeploymentRecord,deployment_id);dep.state_json={**state,'last_tick_at':now().isoformat()};db.commit()
    if not trigger or not symbol:return {'triggered':False,'symbol':symbol,'mode':dep.mode}
    quote_amount=float(cfg.get('quote_amount',100.0));fee_bps=float(cfg.get('fee_bps',10))
    if dep.mode=='paper':
        with SessionLocal() as db:return {'triggered':True,'mode':'paper',**(await paper_buy(db,ensure_default_account(db),symbol=symbol,quote_amount=quote_amount,signal_id=signal_id,fee_bps=fee_bps))}
    if not confirm_live or not bool(cfg.get('live_confirmed',False)):raise ValueError('Live deployment requires live_confirmed plus explicit confirmation')
    venue=await best_venue(symbol,[x for x in SUPPORTED if credentials_present(x)]);best=venue.get('best')
    if not best:raise ValueError('No connected broker lists this symbol')
    price=float(best.get('ask') or best.get('last'));amount=quote_amount/price
    order=await place_live_order(broker_id=best['broker_id'],symbol=symbol,side='buy',amount=amount,order_type='market')
    return {'triggered':True,'mode':'live','broker':best['broker_id'],'order':order}
