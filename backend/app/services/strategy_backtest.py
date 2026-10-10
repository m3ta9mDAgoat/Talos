from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy.orm import Session

from app.core.enums import MarketType, Side, SignalStatus
from app.db.session import SessionLocal
from app.models.raw_event import RawEventRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.models.strategy import StrategyConfigRecord
from app.models.research import StrategyRunRecord, StrategyTradeRecord
from app.schemas.signal import EntrySpec, NormalizedSignal, StopSpec
from app.services.backtest import signal_backtester
from app.services.market_data import market_data_resolver
from app.services.strategy_engine import simulate_technical
from app.services.osint_features import bar_osint_features, OSINT_FEATURE_NAMES
from app.services.trade_metrics import summarize_returns, trade_return_pct


def now(): return datetime.now(timezone.utc)

def _parse_dt(value: Any) -> datetime:
    text=str(value)
    if len(text)==10: text += 'T00:00:00+00:00'
    dt=datetime.fromisoformat(text.replace('Z','+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _strategy(db:Session,sid:int)->StrategyConfigRecord:
    s=db.get(StrategyConfigRecord,sid)
    if not s: raise ValueError('Strategy not found')
    return s

def create_strategy_run(strategy_config_id:int,config:dict[str,Any])->StrategyRunRecord:
    with SessionLocal() as db:
        _strategy(db,strategy_config_id)
        r=StrategyRunRecord(strategy_config_id=strategy_config_id,status='queued',stage='queued',config_json=config,state_json={},summary_json={})
        db.add(r); db.commit(); db.refresh(r); return r

def _signal(row:SignalRecord)->NormalizedSignal:
    return NormalizedSignal(strategy_id=row.strategy_id,strategy_version=row.strategy_version,source=row.source,source_id=row.source_id,source_message_id=row.source_message_id,timestamp=row.timestamp,market_type=MarketType(row.market_type),symbol=row.symbol,quote=(row.symbol.split('/')[1] if '/' in row.symbol else None),side=Side(row.side),entry=EntrySpec(**row.entry) if row.entry else None,stop=StopSpec(**row.stop) if row.stop else None,targets=list(row.targets or []),confidence=row.confidence,status=SignalStatus(row.status),raw_text=row.raw_text,metadata=row.metadata_json or {})

async def _candles(symbol:str,timeframe:str,start:datetime,end:datetime,exchanges:list[str]):
    markets=await market_data_resolver.resolve(symbol,exchanges)
    for m in markets:
        cs=await market_data_resolver.candles(exchange_id=m.exchange,symbol=m.symbol,timeframe=timeframe,start=start,end=end,max_bars=50000)
        if cs:return m.exchange,m.symbol,cs
    return None,None,[]

def summarize_run(db:Session,run:StrategyRunRecord)->dict[str,Any]:
    rows=db.query(StrategyTradeRecord).filter(StrategyTradeRecord.run_id==run.id).order_by(StrategyTradeRecord.entered_at.asc()).all()
    returns=[float(x.return_pct) for x in rows if x.return_pct is not None]
    fraction=float((run.config_json or {}).get('portfolio_fraction',0.1))
    base=summarize_returns(returns,fraction); by_symbol={}
    for row in rows:
        if row.return_pct is None:continue
        by_symbol.setdefault(row.symbol,[]).append(float(row.return_pct))
    base['by_symbol']={k:summarize_returns(v,fraction) for k,v in by_symbol.items()}; base['tested_rows']=len(rows)
    return base

async def advance_strategy_run(run_id:int,batch_size:int=8)->StrategyRunRecord:
    with SessionLocal() as db:
        run=db.get(StrategyRunRecord,run_id)
        if not run:raise ValueError('Run not found')
        strat=_strategy(db,run.strategy_config_id); strategy_id=run.strategy_config_id; params=dict(strat.parameters_json or {}); kind=params.get('kind','signal_copy'); definition=dict(params.get('definition') or {}); cfg=dict(run.config_json or {}); state=dict(run.state_json or {})
        timeframe=cfg.get('timeframe') or definition.get('timeframe') or '1h'; exchanges=cfg.get('exchanges') or ['binance','okx','bybit','bitget','kucoin','mexc','bingx']; fee=float(cfg.get('fee_bps',10)); slip=float(cfg.get('slippage_bps',5))
        run.status='running'; run.stage=f'backtest:{kind}'; run.error=None; db.commit()
    try:
        if kind=='technical':
            symbols=definition.get('symbols') or cfg.get('symbols') or ['BTC/USDT']; idx=int(state.get('symbol_index',0))
            for _ in range(batch_size):
                if idx>=len(symbols):break
                symbol=symbols[idx]; start=_parse_dt(cfg.get('start')) if cfg.get('start') else now()-timedelta(days=365); end=_parse_dt(cfg.get('end')) if cfg.get('end') else now()
                venue,_,cs=await _candles(symbol,timeframe,start,end,exchanges)
                osint_bars = None
                if cs and any(c.get('feature') in OSINT_FEATURE_NAMES for c in (definition.get('conditions') or [])):
                    # A single snapshot for the full replay, never a SQL query per candle.
                    with SessionLocal() as osint_db:
                        osint_bars, osint_warning = bar_osint_features(osint_db, cs, symbol,
                            int(definition.get('osint_lookback_hours',24)))
                    if osint_warning:
                        state.setdefault('osint_warnings', {})[symbol] = osint_warning
                trades=simulate_technical(cs,definition,fee,slip,osint_bars) if cs else []
                with SessionLocal() as db:
                    for t in trades: db.add(StrategyTradeRecord(run_id=run_id,strategy_config_id=strategy_id,symbol=symbol,venue=venue,entered_at=t['entered_at'],exited_at=t['exited_at'],entry_price=t['entry_price'],exit_price=t['exit_price'],return_pct=t['return_pct'],status=t['status'],result_json=t))
                    rr=db.get(StrategyRunRecord,run_id); idx+=1; rr.state_json={'symbol_index':idx}; rr.progress=idx/max(1,len(symbols)); db.commit()
            complete=idx>=len(symbols)
        elif kind=='source_event':
            source_id=int(definition.get('source_id') or 0); symbols=definition.get('symbols') or ['BTC/USDT']
            with SessionLocal() as db:
                source=db.get(SourceRecord,source_id); last=int(state.get('raw_id',0))
                if not source:raise ValueError('source_event strategy needs a valid source_id')
                q=db.query(RawEventRecord).filter(RawEventRecord.source_identifier==source.identifier,RawEventRecord.id>last).order_by(RawEventRecord.id.asc()).limit(batch_size).all(); events=[{'id':x.id,'occurred_at':x.occurred_at,'text':x.text or ''} for x in q]
            kws=[x.lower() for x in definition.get('keywords_any') or []]; none=[x.lower() for x in definition.get('keywords_none') or []]
            for ev in events:
                state['raw_id']=ev['id']; text=ev['text'].lower()
                if kws and not any(k in text for k in kws):continue
                if any(k in text for k in none):continue
                for symbol in symbols:
                    start=ev['occurred_at']; end=start+timedelta(days=int((cfg.get('horizon_days') or 7)))
                    venue,_,cs=await _candles(symbol,timeframe,start,end,exchanges)
                    if len(cs)<2:continue
                    entry=cs[0].open*(1+slip/10000); tp=entry*(1+float(definition.get('take_profit_pct',5))/100); sl=entry*(1-float(definition.get('stop_loss_pct',2.5))/100); status='timeout'; exit_px=cs[-1].close; j=len(cs)-1
                    for n,c in enumerate(cs):
                        if c.low<=sl:status='stop';exit_px=sl;j=n;break
                        if c.high>=tp:status='target';exit_px=tp;j=n;break
                    ret=((exit_px/entry)-1-2*(fee+slip)/10000)*100
                    with SessionLocal() as db: db.add(StrategyTradeRecord(run_id=run_id,strategy_config_id=strategy_id,source_ref=str(ev['id']),symbol=symbol,venue=venue,entered_at=cs[0].timestamp,exited_at=cs[j].timestamp,entry_price=entry,exit_price=exit_px,return_pct=ret,status=status,result_json={'event_text':ev['text'][:500]})); db.commit()
            with SessionLocal() as db:
                rr=db.get(StrategyRunRecord,run_id); rr.state_json=state
                total=db.query(RawEventRecord).filter(RawEventRecord.source_identifier==source.identifier).count()
                done=db.query(RawEventRecord).filter(RawEventRecord.source_identifier==source.identifier,RawEventRecord.id<=int(state.get('raw_id',0))).count() if state.get('raw_id') else 0
                rr.progress=(done/total) if total else 1.0; db.commit()
            complete=len(events)<batch_size
        else:
            source_id=int(definition.get('source_id') or 0); min_conf=float(definition.get('minimum_confidence',0.0))
            with SessionLocal() as db:
                source=db.get(SourceRecord,source_id); last=int(state.get('signal_id',0))
                if not source:raise ValueError('signal_copy strategy needs a valid source_id')
                rows=db.query(SignalRecord).filter(SignalRecord.source_id==source.identifier,SignalRecord.id>last,SignalRecord.status=='parsed',SignalRecord.side=='buy',SignalRecord.confidence>=min_conf).order_by(SignalRecord.id.asc()).limit(batch_size).all(); ids=[r.id for r in rows]
            for sid in ids:
                state['signal_id']=sid
                with SessionLocal() as db: r=db.get(SignalRecord,sid); sig=_signal(r) if r else None
                if not sig or not sig.entry or not sig.targets:continue
                if definition.get('require_stop',False) and not sig.stop:continue
                start=sig.timestamp; end=start+timedelta(days=int(cfg.get('horizon_days',14))); venue,_,cs=await _candles(sig.symbol,timeframe,start,end,exchanges)
                if not cs:continue
                result=signal_backtester.run(sig,cs,candle_timeframe=timeframe,conservative_intrabar=True); ret=trade_return_pct(sig,result,final_price=cs[-1].close,fee_bps=fee,slippage_bps=slip)
                with SessionLocal() as db: db.add(StrategyTradeRecord(run_id=run_id,strategy_config_id=strategy_id,source_ref=str(sid),symbol=sig.symbol,venue=venue,entered_at=result.entry_at,exited_at=result.stop_at or cs[-1].timestamp,entry_price=result.entry_price,exit_price=result.exit_price or cs[-1].close,return_pct=ret,status='tested' if result.entry_triggered else 'not_triggered',result_json=result.model_dump(mode='json'))); db.commit()
            with SessionLocal() as db:
                rr=db.get(StrategyRunRecord,run_id); rr.state_json=state
                total=db.query(SignalRecord).filter(SignalRecord.source_id==source.identifier,SignalRecord.status=='parsed',SignalRecord.side=='buy',SignalRecord.confidence>=min_conf).count()
                done=db.query(SignalRecord).filter(SignalRecord.source_id==source.identifier,SignalRecord.status=='parsed',SignalRecord.side=='buy',SignalRecord.confidence>=min_conf,SignalRecord.id<=int(state.get('signal_id',0))).count() if state.get('signal_id') else 0
                rr.progress=(done/total) if total else 1.0; db.commit()
            complete=len(ids)<batch_size
        with SessionLocal() as db:
            rr=db.get(StrategyRunRecord,run_id); rr.summary_json=summarize_run(db,rr)
            if complete:
                rr.status='complete';rr.stage='complete';rr.progress=1.0;rr.completed_at=now()
                strategy=db.get(StrategyConfigRecord,rr.strategy_config_id)
                if strategy and not (strategy.parameters_json or {}).get('_temporary') and strategy.stage=='draft': strategy.stage='backtested'
            db.commit();db.refresh(rr);return rr
    except Exception as exc:
        with SessionLocal() as db:
            rr=db.get(StrategyRunRecord,run_id);rr.status='error';rr.stage='error';rr.error=f'{type(exc).__name__}: {exc}';db.commit();db.refresh(rr);return rr
