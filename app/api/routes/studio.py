from __future__ import annotations

import hashlib
import secrets
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.enums import SourceType, SourceStatus
from app.db.session import get_db
from app.models.raw_event import RawEventRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.models.strategy import StrategyConfigRecord
from app.models.research import ResearchArtifactRecord, StrategyRunRecord, StrategyTradeRecord, OptimizationRunRecord, StrategyDeploymentRecord
from app.services.security import save_secret
from app.services.universal_sources import sync_source, test_source
from app.services.research_service import search_crossref, draft_strategy
from app.services.strategy_backtest import create_strategy_run, advance_strategy_run
from app.services.generic_optimizer import create_optimization, advance_optimization
from app.services.deployments import tick as deployment_tick
from app.services.runtime_wake import wake_sources

router=APIRouter(tags=['studio'])
hooks=APIRouter(tags=['hooks'])

CATALOG=[
 {'adapter':'telegram','label':'Telegram','source_type':'telegram','history':True,'fields':['identifier'],'description':'Channels/groups via dedicated Telegram user session.'},
 {'adapter':'rss','label':'RSS / Atom','source_type':'rss','history':'feed-dependent','fields':['url'],'description':'News feeds and announcement feeds.'},
 {'adapter':'http_json','label':'HTTP JSON API','source_type':'custom','history':'api-dependent','fields':['url','items_path','id_field','text_field','time_field'],'description':'Any REST/JSON source, including news and social APIs.'},
 {'adapter':'web','label':'Web scraper','source_type':'custom','history':False,'fields':['url','item_selector','text_selector','link_selector'],'description':'CSS-selector scraper for permitted public pages.'},
 {'adapter':'webhook','label':'Webhook','source_type':'custom','history':False,'fields':[],'description':'Push arbitrary events into Talos.'},
 {'adapter':'x_api','label':'X / social API','source_type':'custom','history':'plan-dependent','fields':['url','items_path','id_field','text_field','time_field'],'description':'Official/social API through generic JSON adapter; secret headers stay encrypted.'},
]

class SourceStudioCreate(BaseModel):
    name:str; adapter:str; identifier:str=''; config:dict[str,Any]=Field(default_factory=dict); secret_headers:dict[str,str]=Field(default_factory=dict)

class StrategyWrite(BaseModel):
    name:str; kind:str; version:str='1.0'; stage:str='draft'; enabled:bool=True; definition:dict[str,Any]=Field(default_factory=dict); optimization_space:dict[str,list[float]]=Field(default_factory=dict); notes:str=''

class RunCreate(BaseModel):
    timeframe:str='1h'; start:str|None=None; end:str|None=None; horizon_days:int=14; fee_bps:float=10; slippage_bps:float=5; portfolio_fraction:float=0.10; exchanges:list[str]=Field(default_factory=lambda:['binance','okx','bybit','bitget','kucoin','mexc'])

class ResearchSave(BaseModel):
    title:str; url:str|None=None; doi:str|None=None; abstract:str|None=None; notes:str|None=None; metadata:dict[str,Any]=Field(default_factory=dict)

class DraftRequest(BaseModel):
    name:str='Research strategy'; text:str

class OptimizeRequest(BaseModel):
    run_config:dict[str,Any]=Field(default_factory=dict); max_trials:int=Field(default=30,ge=1,le=100)

class DeploymentWrite(BaseModel):
    strategy_config_id:int; mode:str='paper'; enabled:bool=False; config:dict[str,Any]=Field(default_factory=lambda:{'quote_amount':100.0,'fee_bps':10.0,'live_confirmed':False})

@router.get('/studio/source-catalog')
def source_catalog():return CATALOG

@router.post('/studio/sources')
def create_source(req:SourceStudioCreate,db:Session=Depends(get_db)):
    if req.adapter not in {x['adapter'] for x in CATALOG}:raise HTTPException(400,'Unknown source adapter')
    type_map={'telegram':SourceType.TELEGRAM,'rss':SourceType.RSS}; typ=type_map.get(req.adapter,SourceType.CUSTOM)
    identifier=req.identifier or f'webhook:{secrets.token_hex(8)}';cfg={**req.config,'adapter':req.adapter}
    token=None
    if req.adapter=='webhook':
        token=secrets.token_urlsafe(32);cfg['webhook_token_hash']=hashlib.sha256(token.encode()).hexdigest()
    row=SourceRecord(name=req.name,type=typ,identifier=identifier,enabled=True,status=SourceStatus.READY,config=cfg);db.add(row);db.commit();db.refresh(row);wake_sources()
    if req.secret_headers:save_secret(f'source:{row.id}',{'headers':req.secret_headers})
    return {'id':row.id,'name':row.name,'adapter':req.adapter,'identifier':row.identifier,'webhook_token':token}

@router.post('/studio/sources/{source_id}/test')
async def source_test(source_id:int):
    try:return await test_source(source_id)
    except Exception as e:raise HTTPException(400,str(e)) from e

@router.post('/studio/sources/{source_id}/sync')
async def source_sync(source_id:int,db:Session=Depends(get_db)):
    try:
        result=await sync_source(source_id)
        source=db.get(SourceRecord,source_id)
        if source and ((source.config or {}).get('adapter')=='telegram' or str(getattr(source.type,'value',source.type))=='telegram'):
            from app.services.processing import process_source
            result['processing']=process_source(source_id)
        return result
    except Exception as e:raise HTTPException(400,str(e)) from e

@router.put('/studio/sources/{source_id}/secret')
def source_secret(source_id:int,values:dict[str,Any],db:Session=Depends(get_db)):
    if not db.get(SourceRecord,source_id):raise HTTPException(404,'Source not found')
    save_secret(f'source:{source_id}',values);return {'ok':True}

@hooks.post('/hooks/{source_id}')
def webhook(source_id:int,payload:dict[str,Any],x_talos_token:str|None=Header(default=None),db:Session=Depends(get_db)):
    source=db.get(SourceRecord,source_id)
    if not source or (source.config or {}).get('adapter')!='webhook':raise HTTPException(404,'Webhook source not found')
    expected=(source.config or {}).get('webhook_token_hash')
    if not x_talos_token or hashlib.sha256(x_talos_token.encode()).hexdigest()!=expected:raise HTTPException(401,'Bad webhook token')
    external=str(payload.get('id') or secrets.token_hex(12));dedupe=hashlib.sha256(f'{source_id}:{external}'.encode()).hexdigest();text=str(payload.get('text') or payload.get('title') or '')
    row=RawEventRecord(dedupe_key=dedupe,source_type='custom',source_identifier=source.identifier,external_id=external,occurred_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc),event_type='webhook',text=text,payload=payload,metadata_json={'source_id':source_id,'adapter':'webhook'})
    db.add(row)
    try:db.commit()
    except Exception:db.rollback();return {'ok':True,'duplicate':True}
    return {'ok':True,'raw_event_id':row.id}

@router.get('/research/search')
async def research_search(q:str=Query(...,min_length=2),limit:int=Query(default=10,ge=1,le=20)):
    try:return await search_crossref(q,limit)
    except Exception as e:raise HTTPException(502,str(e)) from e

@router.get('/research/artifacts')
def artifacts(db:Session=Depends(get_db)):
    rows=db.query(ResearchArtifactRecord).order_by(ResearchArtifactRecord.id.desc()).limit(200).all();return [{'id':r.id,'title':r.title,'url':r.url,'doi':r.doi,'abstract':r.abstract,'notes':r.notes,'metadata':r.metadata_json} for r in rows]

@router.post('/research/artifacts')
def save_artifact(req:ResearchSave,db:Session=Depends(get_db)):
    r=ResearchArtifactRecord(title=req.title,url=req.url,doi=req.doi,abstract=req.abstract,notes=req.notes,metadata_json=req.metadata);db.add(r);db.commit();db.refresh(r);return {'id':r.id}

@router.post('/research/draft')
async def make_draft(req:DraftRequest):
    try:return await draft_strategy(req.text,req.name)
    except Exception as e:raise HTTPException(400,str(e)) from e

@router.get('/studio/strategies')
def strategies(db:Session=Depends(get_db)):
    rows=db.query(StrategyConfigRecord).order_by(StrategyConfigRecord.id.desc()).all()
    return [
        {'id':r.id,'strategy_id':r.strategy_id,'name':(r.parameters_json or {}).get('name',r.strategy_id),
         'kind':(r.parameters_json or {}).get('kind','technical'),'version':r.version,'enabled':r.enabled,'stage':r.stage,
         'definition':(r.parameters_json or {}).get('definition',{}),'optimization_space':(r.parameters_json or {}).get('optimization_space',{}),
         'notes':(r.parameters_json or {}).get('notes','')}
        for r in rows
        if not (r.parameters_json or {}).get('_temporary') and (r.parameters_json or {}).get('kind') not in {'signal_copy'}
    ]

@router.post('/studio/strategies')
def create_strategy(req:StrategyWrite,db:Session=Depends(get_db)):
    slug=''.join(c.lower() if c.isalnum() else '-' for c in req.name).strip('-') or f'strategy-{secrets.token_hex(4)}';slug=f'{slug}-{secrets.token_hex(2)}'
    row=StrategyConfigRecord(strategy_id=slug,version=req.version,enabled=req.enabled,stage=req.stage,parameters_json={'name':req.name,'kind':req.kind,'definition':req.definition,'optimization_space':req.optimization_space,'notes':req.notes});db.add(row);db.commit();db.refresh(row);return {'id':row.id,'strategy_id':row.strategy_id}

@router.put('/studio/strategies/{strategy_id}')
def update_strategy(strategy_id:int,req:StrategyWrite,db:Session=Depends(get_db)):
    row=db.get(StrategyConfigRecord,strategy_id)
    if not row:raise HTTPException(404,'Strategy not found')
    row.version=req.version;row.enabled=req.enabled;row.stage=req.stage;row.parameters_json={'name':req.name,'kind':req.kind,'definition':req.definition,'optimization_space':req.optimization_space,'notes':req.notes};db.commit();return {'ok':True}

@router.delete('/studio/strategies/{strategy_id}')
def delete_strategy(strategy_id:int,db:Session=Depends(get_db)):
    row=db.get(StrategyConfigRecord,strategy_id)
    if not row:raise HTTPException(404,'Strategy not found')
    db.delete(row);db.commit();return {'ok':True}

@router.post('/strategy-runs/{strategy_id}')
def new_run(strategy_id:int,req:RunCreate):
    try:
        r=create_strategy_run(strategy_id,req.model_dump());return {'id':r.id,'status':r.status,'progress':r.progress}
    except Exception as e:raise HTTPException(400,str(e)) from e

@router.post('/strategy-runs/run/{run_id}/advance')
async def advance(run_id:int,batch_size:int=Query(default=8,ge=1,le=50)):
    r=await advance_strategy_run(run_id,batch_size);return {'id':r.id,'status':r.status,'stage':r.stage,'progress':r.progress,'summary':r.summary_json,'error':r.error}

@router.get('/strategy-runs')
def runs(limit:int=Query(default=50,ge=1,le=200),db:Session=Depends(get_db)):
    rows=db.query(StrategyRunRecord).order_by(StrategyRunRecord.id.desc()).limit(limit).all();return [{'id':r.id,'strategy_config_id':r.strategy_config_id,'status':r.status,'stage':r.stage,'progress':r.progress,'summary':r.summary_json,'error':r.error,'config':r.config_json,'created_at':r.created_at} for r in rows]

@router.get('/strategy-runs/{run_id}/trades')
def run_trades(run_id:int,limit:int=Query(default=500,ge=1,le=5000),db:Session=Depends(get_db)):
    rows=db.query(StrategyTradeRecord).filter(StrategyTradeRecord.run_id==run_id).order_by(StrategyTradeRecord.id.desc()).limit(limit).all();return [{'id':r.id,'symbol':r.symbol,'venue':r.venue,'entered_at':r.entered_at,'exited_at':r.exited_at,'entry_price':r.entry_price,'exit_price':r.exit_price,'return_pct':r.return_pct,'status':r.status,'result':r.result_json} for r in rows]

@router.post('/strategy-optimizer/{strategy_id}')
def optimize(strategy_id:int,req:OptimizeRequest):
    try:
        r=create_optimization(strategy_id,req.run_config,req.max_trials);return {'id':r.id,'status':r.status,'progress':r.progress}
    except Exception as e:raise HTTPException(400,str(e)) from e

@router.post('/strategy-optimizer/run/{opt_id}/advance')
async def optimize_advance(opt_id:int,run_batch_size:int=Query(default=20,ge=1,le=50)):
    try:
        r=await advance_optimization(opt_id,run_batch_size);return {'id':r.id,'status':r.status,'progress':r.progress,'best':r.best_json,'trials':r.trials_json,'error':r.error}
    except Exception as e:raise HTTPException(400,str(e)) from e

@router.get('/strategy-optimizer')
def optimization_runs(db:Session=Depends(get_db)):
    rows=db.query(OptimizationRunRecord).order_by(OptimizationRunRecord.id.desc()).limit(100).all();return [{'id':r.id,'strategy_config_id':r.strategy_config_id,'status':r.status,'progress':r.progress,'objective':r.objective,'best':r.best_json,'trials':r.trials_json,'error':r.error,'created_at':r.created_at} for r in rows]

@router.get('/deployments')
def deployments(db:Session=Depends(get_db)):
    rows=db.query(StrategyDeploymentRecord).order_by(StrategyDeploymentRecord.id.desc()).all();return [{'id':r.id,'strategy_config_id':r.strategy_config_id,'mode':r.mode,'enabled':r.enabled,'config':r.config_json,'state':r.state_json,'updated_at':r.updated_at} for r in rows]

@router.post('/deployments')
def deployment_save(req:DeploymentWrite,db:Session=Depends(get_db)):
    strategy=db.get(StrategyConfigRecord,req.strategy_config_id)
    if not strategy:raise HTTPException(404,'Strategy not found')
    if req.mode=='live' and strategy.stage not in {'approved','live'}:
        raise HTTPException(400,'Live deployment requires strategy stage APPROVED or LIVE')
    row=db.query(StrategyDeploymentRecord).filter(StrategyDeploymentRecord.strategy_config_id==req.strategy_config_id,StrategyDeploymentRecord.mode==req.mode).first()
    if not row:
        params=dict(strategy.parameters_json or {});kind=params.get('kind','signal_copy');definition=dict(params.get('definition') or {});initial_state={}
        source_id=int(definition.get('source_id') or 0)
        source=db.get(SourceRecord,source_id) if source_id else None
        if source and kind=='signal_copy':
            latest=db.query(SignalRecord).filter(SignalRecord.source_id==source.identifier).order_by(SignalRecord.id.desc()).first();initial_state={'signal_id':latest.id if latest else 0}
        elif source and kind=='source_event':
            latest=db.query(RawEventRecord).filter(RawEventRecord.source_identifier==source.identifier).order_by(RawEventRecord.id.desc()).first();initial_state={'raw_id':latest.id if latest else 0}
        row=StrategyDeploymentRecord(strategy_config_id=req.strategy_config_id,mode=req.mode,state_json=initial_state);db.add(row)
    row.enabled=req.enabled;row.config_json=req.config;db.commit();db.refresh(row);return {'id':row.id,'enabled':row.enabled,'mode':row.mode}

@router.post('/deployments/{deployment_id}/tick')
async def deployment_run(deployment_id:int,confirm_live:bool=False):
    try:return await deployment_tick(deployment_id,confirm_live)
    except Exception as e:raise HTTPException(400,str(e)) from e
